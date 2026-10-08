<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref } from "vue";
import { ElMessage } from "element-plus";
import {
  Connection,
  DataAnalysis,
  Expand,
  Fold,
  Monitor,
  Operation,
  Setting,
  VideoPause,
  VideoPlay,
} from "@element-plus/icons-vue";

import type { GameWindowGeometry, RuntimeSettings } from "../../shared/contracts";
import {
  consumeListenerEnvelope,
  createInitialRuntimeState,
  phaseText,
  recommendationText,
  roleText,
  type DesktopRuntimeState,
  type EventLevel,
} from "./runtime-state";
import { copyRuntimeSettings } from "./runtime-settings";

type Screen = "setup" | "live" | "diagnostics";

const activeScreen = ref<Screen>("setup");
const sidebarCollapsed = ref(true);
const runtime = ref<DesktopRuntimeState>(createInitialRuntimeState());
const runtimeSettings = reactive<RuntimeSettings>({
  modelBackend: "resnet2",
  preplayAdvice: false,
  formalPlayAdvice: true,
  autoPlay: false,
  autoPreplayButtons: false,
});
const serviceBusy = ref(false);
const geometry = ref<GameWindowGeometry>({
  found: false,
  currentSize: null,
  targetSize: [1455, 819],
  effectiveSize: null,
  dpi: null,
  matchMode: "unavailable",
  error: null,
  actionableError: false,
  matches: false,
});
const geometryChecked = ref(false);
const geometryBusy = ref(false);
let unsubscribe: (() => void) | null = null;

const statusTone = computed(() => {
  if (!runtime.value.running) return "info";
  if (runtime.value.snapshot.phase === "uncertain") return "warning";
  return "success";
});
const showingPreplay = computed(() =>
  runtime.value.snapshot.recommendation === null
  && runtime.value.snapshot.recommendationStatus !== "pending"
  && runtime.value.snapshot.recommendationStatus !== "unavailable"
  && runtime.value.preplay.score !== null
);
const advice = computed(() => {
  if (runtime.value.snapshot.recommendationStatus === "pending") return "正在生成新建议";
  if (runtime.value.snapshot.recommendationStatus === "unavailable") return "建议暂不可用";
  if (runtime.value.snapshot.recommendation !== null) {
    return recommendationText(runtime.value.snapshot.recommendation);
  }
  if (showingPreplay.value) return runtime.value.preplay.action;
  return recommendationText(null);
});
const adviceKey = computed(() => showingPreplay.value
  ? `preplay:${runtime.value.preplay.revision}`
  : String(runtime.value.snapshot.recommendationRevision));
const remainingTotal = computed(() =>
  Object.values(runtime.value.snapshot.remainingByRank).reduce((sum, count) => sum + count, 0),
);
const trackedCount = computed(() =>
  Object.keys(runtime.value.snapshot.remainingByRank).length ? 54 - remainingTotal.value : null,
);
const trackerRows = computed(() => {
  const order = ["D", "X", "2", "A", "K", "Q", "J", "10", "9", "8", "7", "6", "5", "4", "3"];
  return order.map((rank) => ({
    rank,
    label: rank === "D" ? "大王" : rank === "X" ? "小王" : rank,
    count: runtime.value.snapshot.remainingByRank[rank] ?? null,
  }));
});
const latencyText = computed(() => {
  const parts: string[] = [];
  if (runtime.value.snapshot.recognitionMs !== null) {
    parts.push(`识别 ${runtime.value.snapshot.recognitionMs.toFixed(1)} ms`);
  }
  if (runtime.value.snapshot.adviceMs !== null) {
    parts.push(`正式建议 ${runtime.value.snapshot.adviceMs.toFixed(1)} ms`);
  }
  if (showingPreplay.value) {
    const preplayParts = [runtime.value.preplay.detail];
    if (runtime.value.preplay.adviceMs !== null) {
      preplayParts.push(`局前流程 ${runtime.value.preplay.adviceMs.toFixed(1)} ms`);
    }
    return preplayParts.join(" · ");
  }
  if (runtime.value.snapshot.recommendationStatus === "pending") return "正式建议计时中…";
  return parts.join(" · ") || "尚未产生建议耗时";
});
const runtimeLabel = computed(() => runtime.value.running
  ? "JAVA RUNTIME · DESKTOP.V1 WS"
  : "JAVA + PYTHON 服务待启动");
const geometryStartable = computed(() =>
  geometryChecked.value && geometry.value.matches && geometry.value.error === null,
);

async function inspectGeometry(): Promise<void> {
  if (geometryBusy.value) return;
  geometryBusy.value = true;
  try {
    geometry.value = await window.desktopApi.inspectGameWindow();
    // 用户明确点击检测后，尺寸未命中立即调整并回读；未精确命中不得放行服务启动。
    if (geometry.value.found && geometry.value.error === null && !geometry.value.matches) {
      geometry.value = await window.desktopApi.adjustGameWindow();
    }
    geometryChecked.value = true;
  } catch (error) {
    geometry.value = {
      ...geometry.value,
      error: error instanceof Error ? error.message : String(error),
      actionableError: false,
      matches: false,
    };
    geometryChecked.value = true;
  } finally {
    geometryBusy.value = false;
  }
}

async function startServices(): Promise<void> {
  if (serviceBusy.value || runtime.value.running) return;
  if (!geometryStartable.value) {
    ElMessage.warning("请先完成客户端自动调整，并确认精确命中 1455 × 819");
    return;
  }
  serviceBusy.value = true;
  try {
    const result = await window.desktopApi.startRuntime(copyRuntimeSettings(runtimeSettings));
    if (!result.running) {
      ElMessage.error(result.message);
      return;
    }
    ElMessage.success(result.message);
    activeScreen.value = "live";
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : String(error));
  } finally {
    serviceBusy.value = false;
  }
}

async function stopServices(): Promise<void> {
  try {
    const result = await window.desktopApi.stopRuntime();
    ElMessage.info(result.message);
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : String(error));
  }
  activeScreen.value = "setup";
}

function showLiveAdvice(): void {
  if (runtime.value.running) activeScreen.value = "live";
}

function eventClass(level: EventLevel): string {
  return `tone-${level}`;
}

function localTime(timestamp: string): string {
  const time = new Date(timestamp);
  return Number.isNaN(time.getTime()) ? "--:--:--" : time.toLocaleTimeString("zh-CN", { hour12: false });
}

onMounted(() => {
  unsubscribe = window.desktopApi.onListenerMessage((message) => {
    runtime.value = consumeListenerEnvelope(runtime.value, message);
  });
});

onBeforeUnmount(() => {
  unsubscribe?.();
});
</script>

<template>
  <div class="desktop-shell" :class="{ 'is-collapsed': sidebarCollapsed }">
    <aside class="sidebar">
      <div class="brand">
        <div class="brand-mark">D</div>
        <div class="brand-copy">
          <strong>DouZero</strong>
          <span>Advisor Desktop</span>
        </div>
        <el-button
          class="sidebar-toggle"
          text
          circle
          :title="sidebarCollapsed ? '展开菜单' : '收起菜单'"
          :aria-label="sidebarCollapsed ? '展开菜单' : '收起菜单'"
          @click="sidebarCollapsed = !sidebarCollapsed"
        >
          <el-icon><Expand v-if="sidebarCollapsed" /><Fold v-else /></el-icon>
        </el-button>
      </div>
      <el-menu :default-active="activeScreen" :collapse="sidebarCollapsed" class="nav-menu" @select="activeScreen = $event as Screen">
        <el-menu-item index="setup"><el-icon><Setting /></el-icon><span>启动配置</span></el-menu-item>
        <el-menu-item index="live"><el-icon><Monitor /></el-icon><span>实时建议</span></el-menu-item>
        <el-menu-item index="diagnostics"><el-icon><DataAnalysis /></el-icon><span>诊断与日志</span></el-menu-item>
      </el-menu>
      <div class="safety-card">
        <span>SAFETY MODE</span>
        <strong>正式出牌自动提交已禁用</strong>
        <p>正式出牌仍只自动选牌；局前按钮可单独开启，且不叫、不抢、不加倍永远不会点击。</p>
      </div>
    </aside>

    <main class="workspace">
      <header class="topbar">
        <div>
          <span class="eyebrow">LOCAL DESKTOP · {{ runtimeLabel }}</span>
          <h1>{{ activeScreen === 'setup' ? '服务与监控' : activeScreen === 'live' ? '实时建议' : '诊断与日志' }}</h1>
        </div>
        <el-tag :type="statusTone" effect="dark" round>
          {{ runtime.running ? phaseText(runtime.snapshot.phase) : '未启动' }}
        </el-tag>
      </header>

      <section v-if="activeScreen === 'setup'" class="screen-grid setup-grid">
        <el-card v-if="!geometryStartable" class="panel setup-step geometry-panel" shadow="never">
          <template #header><div class="panel-title"><Connection /><span class="step-title"><small>第一步</small>检测游戏窗口</span></div></template>
          <p class="step-intro">先检测欢乐斗地主客户端；发现尺寸不符会立即自动调整，并且必须精确命中标定才能启动服务。</p>
          <div class="geometry-state" :class="{ ready: geometryStartable, error: geometryChecked && (!!geometry.error || !geometry.found) }">
            <span class="status-dot"></span>
            <div>
              <strong>{{ !geometryChecked ? '尚未检测游戏窗口' : geometry.error ? '客户端自动调整失败' : geometry.matches ? '尺寸已精确命中，可以启动服务' : geometry.found ? '自动调整后仍未命中标定' : '未找到游戏窗口' }}</strong>
              <span v-if="!geometryChecked">请点击下方按钮开始检测</span>
              <span v-else-if="geometry.currentSize">报告尺寸 {{ geometry.currentSize[0] }} × {{ geometry.currentSize[1] }}</span>
              <span v-else>未读取到客户区尺寸</span>
            </div>
          </div>
          <div class="geometry-values">
            <div><span>Windows 缩放</span><strong>{{ !geometryChecked || geometry.dpi === null ? '未检测' : `${Math.round(geometry.dpi / 96 * 100)}%（${geometry.dpi} DPI）` }}</strong></div>
            <div><span>标定物理像素</span><strong>{{ geometry.targetSize[0] }} × {{ geometry.targetSize[1] }}</strong></div>
            <div v-if="geometry.effectiveSize"><span>DPI 等效物理尺寸</span><strong>{{ geometry.effectiveSize[0] }} × {{ geometry.effectiveSize[1] }}</strong></div>
            <div><span>检查状态</span><strong>{{ !geometryChecked ? '尚未检测' : geometry.error || (geometry.matches ? '通过' : geometry.found ? '未通过' : '未找到') }}</strong></div>
          </div>
          <el-button class="geometry-action" type="primary" :loading="geometryBusy" @click="inspectGeometry">{{ geometryChecked ? '重新检测并自动调整客户端' : '检测并自动调整客户端' }}</el-button>
          <p v-if="geometryChecked && geometry.found && !geometry.matches" class="geometry-hint">自动调整后仍未精确命中 {{ geometry.targetSize[0] }} × {{ geometry.targetSize[1] }}；服务不会启动，请保持客户端可见后重新检测。</p>
          <p v-else-if="geometryChecked && !geometry.found" class="geometry-hint">请先打开欢乐斗地主客户端，再重新检测。</p>
        </el-card>

        <el-card v-else class="panel setup-step service-panel" shadow="never">
          <template #header><div class="panel-title"><Operation /><span class="step-title"><small>第二步</small>启动配置服务</span></div></template>
          <div class="setup-gate"><div><span>游戏窗口检测</span><strong>尺寸已精确命中 · {{ geometry.currentSize ? `${geometry.currentSize[0]} × ${geometry.currentSize[1]}` : '1455 × 819' }}</strong></div><el-button text type="primary" :disabled="serviceBusy" @click="inspectGeometry">重新检测并自动调整</el-button></div>
          <div class="service-intro">
            <span class="service-kicker">先打开界面，再按需启动</span>
            <h2>{{ runtime.running ? '服务正在运行' : 'Java 与 Python 尚未启动' }}</h2>
            <p>先选择本次需要的只读建议能力。Electron 会把选择传给 Java Runtime；Java 统一管理识别和已启用的 Python 模型服务，完成 desktop.v1 鉴权前不会显示为已就绪。</p>
          </div>
          <div class="runtime-options">
            <div class="runtime-option fixed-option">
              <div>
                <strong>正式出牌模型</strong>
                <span>ResNet2（当前 Java Runtime 唯一支持的正式模型）</span>
              </div>
              <el-tag type="info" effect="plain">固定</el-tag>
            </div>
            <div class="runtime-option">
              <div>
                <strong>局前建议</strong>
                <span>叫地主、抢地主和加倍；关闭后不启动局前模型</span>
              </div>
              <el-switch v-model="runtimeSettings.preplayAdvice" :disabled="serviceBusy || runtime.running" />
            </div>
            <div class="runtime-option">
              <div>
                <strong>正式出牌建议</strong>
                <span>本方回合的 ResNet2 只读建议；关闭后不启动正式模型</span>
              </div>
              <el-switch v-model="runtimeSettings.formalPlayAdvice" :disabled="serviceBusy || runtime.running" />
            </div>
            <div class="runtime-option">
              <div>
                <strong>自动选牌（渐进模式）</strong>
                <span>系统在游戏画面选中推荐牌，但不会自动点击出牌按钮；您仍需手工确认提交</span>
              </div>
              <el-switch v-model="runtimeSettings.autoPlay" :disabled="serviceBusy || runtime.running" />
            </div>
            <div class="runtime-option">
              <div>
                <strong>自动点击局前正向按钮</strong>
                <span>只点击叫地主、抢地主、加倍或超级加倍；不叫、不抢、不加倍不点击</span>
              </div>
              <el-switch v-model="runtimeSettings.autoPreplayButtons" :disabled="serviceBusy || runtime.running || !runtimeSettings.preplayAdvice" />
            </div>
            <div class="runtime-option fixed-option">
              <div>
                <strong>结算检测</strong>
                <span>始终运行，用于牌局安全收口；不执行换桌或任何自动操作</span>
              </div>
              <el-tag type="success" effect="plain">始终开启</el-tag>
            </div>
          </div>
          <div class="service-list">
            <div><strong>Java Runtime</strong><span>{{ runtime.running ? '已通过 desktop.v1 READY' : '等待启动' }}</span></div>
            <div><strong>Python CV</strong><span>{{ runtime.running ? '由 Java 生命周期统一管理' : '等待 Java 拉起' }}</span></div>
            <div><strong>局前模型</strong><span>{{ runtimeSettings.preplayAdvice ? (runtime.running ? '只读推理服务' : '本次启动') : '本次不启动' }}</span></div>
            <div><strong>正式出牌模型</strong><span>{{ runtimeSettings.formalPlayAdvice ? (runtime.running ? 'ResNet2 只读推理服务' : '本次启动') : '本次不启动' }}</span></div>
            <div><strong>自动选牌执行器</strong><span>{{ runtimeSettings.autoPlay ? (runtime.running ? '渐进模式：仅选牌不提交' : '本次启动') : '本次不启动（只读建议）' }}</span></div>
            <div><strong>局前按钮执行器</strong><span>{{ runtimeSettings.autoPreplayButtons ? (runtime.running ? '只执行正向局前按钮' : '本次启动') : '本次不启动（负向动作不点击）' }}</span></div>
          </div>
          <el-button class="service-button" type="primary" size="large" :loading="serviceBusy" :disabled="runtime.running" @click="startServices">
            <el-icon><VideoPlay /></el-icon>{{ serviceBusy ? '正在启动服务…' : '按本次配置启动服务' }}
          </el-button>
          <p class="service-hint">启动期间可留在此页查看日志；未打开游戏窗口时，服务会安全等待。</p>
          <el-button class="start-button" type="primary" size="large" :disabled="!runtime.running" @click="showLiveAdvice">
            <el-icon><Monitor /></el-icon>查看实时建议
          </el-button>
        </el-card>
      </section>

      <section v-else-if="activeScreen === 'live'" class="live-layout">
        <div class="metric-row">
          <div class="metric"><span>左边出牌</span><strong>{{ runtime.snapshot.leftMove === null ? '—' : runtime.snapshot.leftMove.length ? runtime.snapshot.leftMove.join(' ') : '不出' }}</strong></div>
          <div class="metric"><span>右边出牌</span><strong>{{ runtime.snapshot.rightMove === null ? '—' : runtime.snapshot.rightMove.length ? runtime.snapshot.rightMove.join(' ') : '不出' }}</strong></div>
          <div class="metric"><span>当前轮次</span><strong>{{ runtime.snapshot.round === null ? '—' : `第 ${runtime.snapshot.round} 轮` }}</strong></div>
          <div class="metric"><span>本方角色</span><strong>{{ roleText(runtime.snapshot.role) }}</strong></div>
        </div>

        <el-card class="panel tracker-panel tracker-strip" shadow="never">
          <template #header>
            <div class="panel-title"><DataAnalysis />记牌器<span class="tracker-summary">已出 {{ trackedCount === null ? '—' : trackedCount }} · 桌面剩余 {{ trackedCount === null ? '—' : remainingTotal }}</span></div>
          </template>
          <div class="tracker-grid">
            <div v-for="row in trackerRows" :key="row.rank" :class="['tracker-cell', row.count === 0 ? 'zero' : row.count === 4 ? 'full' : row.count === 1 ? 'one' : '']">
              <strong>{{ row.label }}</strong><span>{{ row.count === null ? '—' : row.count }}</span>
            </div>
          </div>
          <div class="tracker-foot"><span>本方手牌 <strong>{{ runtime.snapshot.myCards.length || '—' }}</strong></span><span>最近领出 <strong>{{ runtime.snapshot.lastMove.join(' ') || '—' }}</strong></span><span>当前阶段 <strong>{{ phaseText(runtime.snapshot.phase) }}</strong></span></div>
        </el-card>

        <div class="live-grid">
          <el-card class="panel advice-panel" shadow="never">
            <template #header><div class="panel-title"><Monitor />当前建议</div></template>
            <div
              :key="adviceKey"
              class="advice-value"
              :class="{
                waiting: runtime.snapshot.recommendation === null
                  && runtime.snapshot.recommendationStatus !== 'pending'
                  && !showingPreplay,
                pending: runtime.snapshot.recommendationStatus === 'pending',
                fresh: runtime.snapshot.recommendationStatus === 'ready' || showingPreplay,
              }"
            >{{ advice }}</div>
            <div class="card-tags" v-if="runtime.snapshot.recommendation?.length">
              <el-tag v-for="(card, index) in runtime.snapshot.recommendation" :key="`${card}-${index}`" effect="plain">{{ card }}</el-tag>
            </div>
            <p>{{ latencyText }}</p>
            <el-button class="stop-button" type="danger" plain @click="stopServices"><el-icon><VideoPause /></el-icon>停止服务</el-button>
          </el-card>

          <el-card class="panel events-panel" shadow="never">
            <template #header><div class="panel-title"><Connection />最近事件<span class="events-summary">最近 10 条</span></div></template>
            <div v-if="!runtime.events.length" class="empty-copy">尚未收到监听事件</div>
            <div v-for="item in runtime.events.slice(0, 10)" :key="`${item.timestamp}-${item.title}`" class="event-row">
              <span :class="['event-tone', eventClass(item.level)]"></span>
              <div><strong>{{ item.title }}</strong><small>{{ localTime(item.timestamp) }} · {{ item.detail }}</small></div>
            </div>
          </el-card>
        </div>
      </section>

      <section v-else class="diagnostics-grid">
        <el-card class="panel" shadow="never">
          <template #header><div class="panel-title"><Connection />组件健康</div></template>
          <div v-for="(value, key) in runtime.diagnostics" :key="key" class="health-row">
            <span>{{ { listener: 'Listener', capture: '画面捕获', recognition: '牌面识别', tracker: '状态跟踪' }[key] || key }}</span>
            <strong>{{ value }}</strong>
          </div>
        </el-card>
        <el-card class="panel log-panel" shadow="never">
          <template #header><div class="panel-title"><DataAnalysis />有界运行日志</div></template>
          <div v-if="!runtime.logs.length" class="empty-copy">启动监控后显示结构化事件与错误摘要</div>
          <div v-for="item in runtime.logs.slice(0, 30)" :key="`${item.timestamp}-${item.stage}-${item.detail}`" class="log-row">
            <time>{{ localTime(item.timestamp) }}</time><span :class="eventClass(item.level)">[{{ item.stage }}]</span><p>{{ item.detail }}</p>
          </div>
        </el-card>
      </section>
    </main>
  </div>
</template>
