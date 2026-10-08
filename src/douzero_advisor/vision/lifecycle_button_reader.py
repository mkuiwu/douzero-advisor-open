"""Recognize Tencent classic-mode lifecycle controls by their rendered text.

Color is used only to find candidate button rectangles.  Lifecycle semantics
come from fixed-font glyph templates captured from the target client, so a
blue ``不出`` control is not confused with the visually similar ``要不起`` state.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from typing import Literal

import cv2
import numpy as np

from douzero_advisor.vision.action_button_reader import ButtonBox


LifecycleButtonText = Literal[
    "reveal",
    "call",
    "no_call",
    "rob",
    "no_rob",
    "double",
    "super_double",
    "no_double",
    "play",
    "pass",
    "hint",
    "cannot_beat",
    "in_play_reveal",
]
LifecycleStage = Literal["preplay", "playing", "unknown"]


@dataclass(frozen=True, slots=True)
class LifecycleButton:
    text: LifecycleButtonText
    box: ButtonBox
    distance: float
    margin: float = 0.0


@dataclass(frozen=True, slots=True)
class LifecycleButtonRead:
    stage: LifecycleStage
    buttons: tuple[LifecycleButton, ...]
    reason: str | None = None

    @property
    def texts(self) -> tuple[LifecycleButtonText, ...]:
        return tuple(button.text for button in self.buttons)

    @property
    def signature(self) -> tuple[object, ...]:
        return (
            self.stage,
            tuple(
                (button.text, button.box.left, button.box.top, button.box.right, button.box.bottom)
                for button in self.buttons
            ),
        )


# Packed 32-pixel-high binary glyph templates.  The sources are target-client
# frames and the user's three lifecycle reference captures; multiplier suffixes
# are deliberately excluded from the templates.
_PACKED_TEMPLATES: dict[str, tuple[int, str]] = {
    "reveal": (79, "00000000aC00003|KK5jJOBUz|BwIR=Ky#B00;jc|G~~5|KI=*{y+bNoPYn|03ZB(4+j|k|G)q|IPe}0apT8;0C;iWJRIYPj{pGh<G^@1#}6JH0pZ8M@N<tpJUj=(kN@EJpa1wgABP|R!T<mN@HjsnKmUXO`0?O4|NMLp2mj&Yz<dAr@E#5U!-s%>fAQcv90Sjv0Dkb}z<4+Z|Na2^;m3gRe~<t81N*~||KR_J|KJDzhadmJ{|C$f9)Au$|AU?%H~@J59DE-K9DHyA;QTo79v(RG@CShZ`@lRrfB*lF0sr@acyRy!{~Q7T{{Zme|Ns5~1ONO1;lqF*000Mo_yfa-03HAU4g=x;j}8Dh00007!~Pr`0B`^R01pHB7&rjn000041K<E40l)wN"),
    "call": (96, "0001B3;+%Q00F=N0002+4geki00Y1PKfnO+4geki00ZCv|KY&!4g?+m00H0t|M9@^4h|jw00Hm-|M9@^4h|jw00Hm-@bSR#4h|k3AOHXU;PJrl9u6P=AOHXU;PJrl9u6P=AOHXU;PJrle~<tE00Zy<;PJrl|KI=K00H0t;PJrl|Ns6T00H0t;PJrlKmYz700H0t;PJrl9)BJl00H0t;PJrl4t^dU00H0t;PJrl4jvvJ00Zy<;PJrl4h|k35C8x8;PJrl4h|k15C8x8;PJ!o4h|pu5C8x8;PL<P4h|pu00Zy<@bmxh4h|pq00H0t@$>)i4h|pS00H0t|MULv4jvu=00H0t|MB4PAKo4Z00H0t|L}nDAKnfJ00H0t@$dlf|K0=-00H0t;NSr8|DOO3AOHXS;NSr8{0{&QKmY&#0002+;1B=)fB*mg0002+01yBEKmY&#0002+00;m4000000001B00IB-00000"),
    "no_call": (72, "00000000004gdfE0000001tov|Na06`~VMs|Ns905B?kvfB*mg01y8j4}bsv{s0gE9uEKm@Bjc0A07_?1K<Dv4-Xy>00-az01poy4*(Cq000jU9uEKyfB*mw4;~KyAHV<r4-Xy>06)M001poy4*-A9000jU9uEQk&wv0A4;~K#|Igq64-Xy>2lL1901poy4-ezR`~VLR9uGgm!2SRa4<0{%1HgX(4-X&z{sX{201poz|NH~M4ge4TKmTw8zytsf|3Ci#1Hb?P5C0zz00Y1P01y8R4*&ze000mF01p5IzyJUb4*(AU1Hb?P4-Nni00Y1P00sa64*&ze0000001p5IzyJUM000jF000000000F"),
    "rob": (96, "3;-Me000aC000009soQ54gd}S00F=N9sqs-4geki00H0t9sqv;4geki00H0t9svJ<4h{|g00H0t9s&P=4h{|g00Hm-|AXV;4h|j;5C8xC|Hr}L4i6vxAOHXU|KEZ59}ge?AOHXUA3p&6KhOXE5C8x89)AFQfA9bP00H0t9)J7%fB*j;00H0t9zXy5KmYz700H0t9v}bkAAg=600H0tAD@HZ4nH0q00H0tKi&Y~4j&#K00H0t|IPs54i6q42mk;0|BeCR4h{|;5C8x8{tf})4h|3g2mk;0{tg4+4h|3e2mk-@9u7a?4i69f00H0t9u7a?4i69T00H0t9u6KH4i6px00H0t9u5E;AI}a400H0t9u5E;AN~#p00H0t9uELKfBygn00H0tJ`ccrf6o994g>H2{ty57d=CH*KmY&#{0IN|U=RQPKmY&#`~&~+00;m6KmY&#0000000aO00001B0000000IB-00000"),
    "no_rob": (77, "00000007_s@Bjb+0000701to<|NsBM0C)iY0RR90{{REP2lxm7|Nr;^9{_*AKmY&#zybIN{{RLL-~a$W@P2*(0Db@f0sn{L_y7a&000mCeh=UP9{>OV5C8B#zya_8004jg0R8|UfB*mk@Bjbs0sa6003Sd9{{SEPfB*pa|Nr0t|K0!q2mk;606+fx004jgf$#(W_wWD*|M&-hKmVV=0Du3$JOlXre}Du3J_F!ChvEJJAMx-%2lzh&_yGS8-}rxl_z%DU@O=Nn_z%E500+b8|BnEE06+jfA0M9p1MmO<0r2<$_y8Y(000k%zz2W;`~Uy|d_Mqu00-az00ZIw|KI>W000302mk*70r&s_2k;;N@Bk0M002LLfByggegFUf@Bjb+"),
    "double": (82, "4gdfE0Pq8V000BP0001d0q_6-0Pp}m03QH+0000y0Dt%o&;S1b06)M#{sZU#|G)qr{}2Cw`Tzg$00;l$!Qea}0q_6;|NQWH4+Fq>0{|c2JRS$Y_#Ob@1Lw~Nf$$y=06YNk^TFW!2ZO*51UP)~I6ndL@B!lvA3P2p&;S46J;R631CR6n|M(B^;q$@c{Qv*{0sJ_8@Ob|K0000#4j()oKfnM001v~5&j*L_00007@!|8p;5+~T00aDZeDF9A5C8rF^Bx~O4g<sg|A6?%htC6m@c;kdJQ2b3!QebR0C*k%@O<!i4-Wty2Y@^uKK=v4zz2cA9tX$&fbj4E-~b1K;s4+~JOFqA0q6Mt_#Y4d{r~{;{5&29!~g$)06hK=2Y~SZ|KI=y9|6F?JRSge00000000aE00aO4"),
    "super_double": (149, "00000000000Du4h000000000C1OLDd0000E00003z#aeq008iR{{Zm+{{VOZ0015U_y7O^2ZR6k2ao^w1Hc3L0PqLE000C25C6b;fB(Q90DtfgAOHLS03ZGy9tVK<J_G*;|A2Y_|L_0+|KRX&JO_dBKmI@b1JD2e0002^2Zw>j91jEk`QYF@9|7<H00Y20JRUgUa3BBA2Z7*t4*<Xz9tY#$|HlV_A0Bu-9|OSn2LZtFJpK>-JUjq!^TFWw4+nre9)J7C;D6!c{sV`e4-de2JOJS5|NK4!{tplE96a!Negos+2aiAg0005te1Cx9=Yz-d|Nr<N@c;1u01pG;JO>Xv96z7`|G<9$JRkpn@E?KTIC<dl{D1%W0r(NX|NI95{0{@e&j%0S0000FfzAix;Cv78@E#s`IDY^D0090DcsLIS{C|gl@bkgIcmMzZ2k>+Gz<7V>o_Gg`o(==S|Nj7U!=KIr!T&t}z&t$gcpM-9{09#n{%{@+0pt7u!RLd(;Q!~~cs%#R;qU+t55OJ|JRS!JfDZ?N=lCE0fB^7*0PuO>@HjXCcsK_?!2kRJe}niB1J9p<!N3E-z#IMmcftSoKj3&CfBX*)03HAUANT+O5C6megTdqf;COfd@Bje+_yAyk{(gQw{~!MY!~g$)00%$+|HH%Y;PL19`1l+j|NH;|cz^%@01kcwjz5QkfZ+fC-~a)D|Nrm+A;1i9_!tZW2Y?R%000000000000000000;m03ZMW"),
    "no_double": (79, "000002LJ#700000000gE000L8|NsB+8~^|S4+H=I|L{BjU;sV`|NsBs_yhmIfA9bQ|HJqP|A7C`f&2g;|DXQ@=YRv?06+gf{s)Hu2fzS-{(L+S1^^!b06%>Acsv9EegFVGdGPS~2m$^80C@A^;qVX#{D1)Q=flV2;2-<I0prhykMF>L_uvD^pAR43f&cH|2ai4;Kfed($M6pxd^~;tKaYp-9z6JX`~dzR2k<<3@bP#*_&x{lc=O@laR1<Z1L5)K!@%MH0Qdug<IjhI!`K1v01t<s4+DpQ1K<E34?aEz4*&<i06rdn{0<%f4}bvpJpcF{JOCd60sr~`@Hlt?J^%;*^Z(#*@Bn-O5C6x<z~SHk_y8V%4~K!nzyJUM7<>*61BZYB0000000096"),
    "play": (73, "0000000006000Mo0000m2Y>(rzyJUSI0L`{0pJh-2b@3u!+`J{00YlI|MS3j4gdk?pa1#bJO=;(^UoeU@E!wz0D0#Q9(WG{zyLhs#}7OQfZzZgarft*1Hf<q5B&fC&x7!I00;m7|L6bz`~U<0{CM;K|Na00|Nc05|Ns900PqhSJODlb004LgpB?}Y0000y1ONX32Y>(oAMyYHm;=BN01y0s_?`ja8~_LYANUUd@DBh3&kvjjfOrRh0ppLJ1He23zyR^b&w=0`0pI|5|Ns2(4*>80Jb(ZH_y>S^031L6|Nj5~`~VId0Q?{S|Na064gfq4|Ns901BU<{2mk;700V=74gdfE1AqX*zz2W;0000006+tP"),
    "pass": (72, "000000001B0000000000@Blym|NZ~~0Pp~R|Ns902Z8V$fB*mg00)8aK7arJ{{RPp@IC+s_y7P0f$%;61K<Dv2Z8WD00-az00)8aJ^&BE000Mp@IC+^fB*mof$%;6KfnM02mk;606)M000;m7{s4c^000O7|Na2~|9}7o|Ns00|Np=M0ssHt2mkNz0005-03YYa_y7O_@Blx@!Ttaa0Pqffhrs^;4*~EV{|CT-01pB1AN&Wv9smyk@E>pozzhHn0q`FH2fzRT4*~EW00+PT01pB19{>ly000jG@E-sNzyJUb|Ns902fzRT5C8xF00+PT01yBF{{REP000mF|Nj63zyJUM0015U000000000C"),
    "hint": (76, "1^@s600000008hl000000000F2mky4000000Qi6Z-~a#s000k%|NZ~~00000_;?540000003U~degFUf000O6fB)bB00000f9L=H0000000;hm|KI=s0001g$H0C7000000Qi0f-~j*s|NjpM|Na0U|NsB+@PGf{0ssI1{||@%{s0gE|Nrs+0000000-azfB*mgfB+zT01yBF|M&m{hrxjV|NsAh0DOEt5BdN8000li$KZbtpMU@W_<TPA@Ok(E03V0J_zwr4{{RE<d=J3z`Ty_$eh0w(4-b$301v=?2jKYl_y7R>2f#lMAKw4~4}g3D;Pe0R008j+zzl!?|Nnpj5BvZJ|Ih#U0094h0Dt&@|9}7o@Bjzrf&cIT03ZMW"),
    "cannot_beat": (96, "00000000000B`^RKmY&u000000B`^RfB*mfKmY&#0QevOfB*mfKmY&#fcPK(KmY&yKmY&#kN=<l0De3GKmY&#pa0+g1b#jM00aC0AOFA)AOHXS00H;_5C4D<KmY&y00Zy<0Qdk8KmY&y00ZCv0Qdk8K7M|D00-az0Qd+0JbpYp01w~*AOHXVK7KxY03YH2KmY&#KmY&y06*t|KmY&#KmY&y0Du3$KmY%LKmY&y0RQ*k4t^d027j0U1ONB<20R`C06zc#2l4U#4?G?LfB*mfAK~!-4?Z3P|NsB~Kf&OCk3Zf6|NsB~e*xehk3aqc|NsB~J^|nl4?o`r2k;-jJOSVa4?my(1MnY!00H0t54;cm2me0+00H0tAN&XZ1ONX300H0tAN&FQ06+f#00Hm-AN&9S06+i000H0tAO8RVKmY&y00H0tKmY&#fB%pE00H0tJb(ZHfA|3W00H0tJRkr6e}Diy000007y<wP"),
    # Optional blue "明牌" shown beside 出牌/提示 on the landlord's opening turn.
    # It is deliberately distinct from both the pre-play reveal control and 不出.
    "in_play_reveal": (73, "0000000002000000000m2Y?^=|Na0690TAV{(t`f1CBrc@BcslfC0xJ|M&l&2fzU1kN^9_&w=0oamOA#;pc$x066D|4)F27cmN!8!-sfy@H_wxIrHbdJpcXx2mb&6-X4Gd00aO3|L^}l|9}Di{(SfUpKrhb|K1!s|IdNo0C*1`9slQm@Blam-=6sMz<dB61ONYcdGI~}5BUH8ygdK^00;bk|DGOy{{RF25Bv`gKmULM?+?xghoAqz0OOC(1H;b&-~e&Q$HD*afbal#|NsC0|G;<vJb(ZH|Np=|03JX8|NsBs9smy<0sbBb;12)?jsQFd1MvTV1BU<}00HoSzyrg84*&r0KfnOtzyp8)I3K_O0AK;Y"),
}

_ORANGE_LABELS: frozenset[LifecycleButtonText] = frozenset(
    {"reveal", "call", "rob", "double", "super_double", "play"}
)
_BLUE_LABELS: frozenset[LifecycleButtonText] = frozenset(
    {
        "no_call",
        "no_rob",
        "no_double",
        "pass",
        "hint",
        "cannot_beat",
        "in_play_reveal",
    }
)
_LAYOUTS: dict[tuple[str, ...], LifecycleStage] = {
    ("reveal",): "preplay",
    ("call", "no_call"): "preplay",
    ("rob", "no_rob"): "preplay",
    ("double", "super_double", "no_double"): "preplay",
    ("play", "hint"): "playing",
    ("play", "pass", "hint"): "playing",
    ("play", "in_play_reveal", "hint"): "playing",
    ("cannot_beat",): "playing",
}

_PREPLAY_LABELS = frozenset(
    {
        "reveal",
        "call",
        "no_call",
        "rob",
        "no_rob",
        "double",
        "super_double",
        "no_double",
    }
)


class LifecycleButtonReader:
    """Read lifecycle text from dynamically located fixed-skin buttons."""

    def __init__(self, *, max_distance: float = 0.34, min_margin: float = 0.035) -> None:
        self._max_distance = float(max_distance)
        self._min_margin = float(min_margin)
        self._templates = _unpack_templates()

    def read(self, frame: np.ndarray) -> LifecycleButtonRead:
        if not isinstance(frame, np.ndarray) or frame.ndim != 3 or frame.shape[2] != 3:
            raise ValueError("frame must be an HxWx3 BGR image")
        if frame.shape[0] <= 0 or frame.shape[1] <= 0:
            raise ValueError("frame dimensions must be non-empty")
        boxes = _find_candidate_boxes(frame)
        if not boxes:
            return LifecycleButtonRead("unknown", (), "no_lifecycle_buttons")
        if max(box.center[1] for box in boxes) - min(box.center[1] for box in boxes) > 9:
            return LifecycleButtonRead("unknown", (), "buttons_not_horizontally_aligned")
        classified_buttons: list[LifecycleButton | None] = [None] * len(boxes)
        unreadable = False
        # ``double`` is a direct orange-template match on the current row.
        # Only after that evidence exists may its paired blue control be read
        # as ``no_double``.  Call/rob/play rows keep the normal blue-label
        # competition and never inherit this rule.
        for index, box in enumerate(boxes):
            if box.palette != "orange":
                continue
            classified = self._classify(frame, box)
            if classified is None:
                unreadable = True
                continue
            classified_buttons[index] = classified
        has_confirmed_double = any(
            button is not None and button.text == "double"
            for button in classified_buttons
        )
        for index, box in enumerate(boxes):
            if box.palette != "blue":
                continue
            classified = self._classify(
                frame,
                box,
                allowed_labels=frozenset({"no_double"}) if has_confirmed_double else None,
            )
            if classified is None:
                # A button animation, glow, or OCR-hostile anti-aliasing can
                # hide one control while a neighboring lifecycle label is
                # already reliable.  Keep the reliable controls; the
                # pre-play advisor only needs the surviving label and the
                # hand snapshot.  Formal-play controls remain fail-closed
                # below because a partial PLAY/PASS/HINT row is ambiguous.
                unreadable = True
                continue
            classified_buttons[index] = classified
        buttons = [button for button in classified_buttons if button is not None]
        if not buttons:
            return LifecycleButtonRead("unknown", (), "button_text_unreadable")
        texts = tuple(button.text for button in buttons)
        stage = _LAYOUTS.get(texts)
        inferred_partial = False
        # During button animation/anti-aliasing, one or more neighboring
        # controls can be missed even though the surviving label is already
        # unambiguous. Keep the frame in the pre-play path so the listener can
        # stabilize it instead of falling through to formal-play bootstrap.
        if stage is None and texts and set(texts).issubset(_PREPLAY_LABELS):
            stage = "preplay"
            inferred_partial = True
        if unreadable and stage != "preplay":
            return LifecycleButtonRead(
                "unknown", tuple(buttons), "button_text_unreadable"
            )
        if stage is None:
            stage = "unknown"
        return LifecycleButtonRead(
            stage,
            tuple(buttons),
            (
                "partial_preplay_layout"
                if inferred_partial
                else None
                if stage != "unknown"
                else f"unexpected_text_layout:{','.join(texts)}"
            ),
        )

    def _classify(
        self,
        frame: np.ndarray,
        box: ButtonBox,
        *,
        allowed_labels: frozenset[LifecycleButtonText] | None = None,
    ) -> LifecycleButton | None:
        mask = _button_text_mask(frame, box)
        if mask is None:
            return None
        allowed = allowed_labels or (
            _ORANGE_LABELS if box.palette == "orange" else _BLUE_LABELS
        )
        distances = sorted(
            (_template_distance(mask, self._templates[name]), name) for name in allowed
        )
        best_distance, best_name = distances[0]
        margin = distances[1][0] - best_distance if len(distances) > 1 else float("inf")
        # The orange `超级加倍` button is substantially wider than the other
        # controls.  The live client rescales its glyph by a few pixels while
        # the animation settles; on real frames this produces a stable
        # nearest-template distance around 0.358 (versus 0.335 on a settled
        # frame).  Keep the generic bound for every other label, but allow a
        # narrow, label-specific extension with the same margin guard.  The
        # executor still requires repeated identical boxes before clicking.
        label_max_distance = (
            self._max_distance if best_name != "super_double" else max(self._max_distance, 0.38)
        )
        minimum_margin = (
            0.02
            if best_name == "no_double"
            else 0.025
            if best_name == "super_double"
            else self._min_margin
        )
        if best_distance > label_max_distance or margin < minimum_margin:
            return None
        return LifecycleButton(
            best_name,
            box,
            round(best_distance, 4),
            round(margin, 4),
        )  # type: ignore[arg-type]


def _unpack_templates() -> dict[str, np.ndarray]:
    templates: dict[str, np.ndarray] = {}
    for name, (width, encoded) in _PACKED_TEMPLATES.items():
        packed = np.frombuffer(base64.b85decode(encoded.encode("ascii")), dtype=np.uint8)
        templates[name] = np.unpackbits(packed)[: 32 * width].reshape(32, width).astype(np.uint8)
    return templates


def _find_candidate_boxes(frame: np.ndarray) -> list[ButtonBox]:
    height, width = frame.shape[:2]
    left, top, right, bottom = (
        int(width * 0.23),
        int(height * 0.54),
        int(width * 0.77),
        int(height * 0.72),
    )
    roi = frame[top:bottom, left:right]
    blue, green, red = (channel.astype(np.int16, copy=False) for channel in cv2.split(roi))
    masks = {
        "orange": ((red > 190) & (green > 120) & ((red - blue) > 70)).astype(np.uint8) * 255,
        "blue": ((blue > 205) & ((blue - green) > 45) & ((blue - red) > 70)).astype(np.uint8) * 255,
    }
    boxes: list[ButtonBox] = []
    for palette, mask in masks.items():
        closed = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((9, 7), dtype=np.uint8))
        count, _labels, stats, _centroids = cv2.connectedComponentsWithStats(closed)
        for index in range(1, count):
            x, y, box_width, box_height, area = (int(value) for value in stats[index])
            fill = area / float(box_width * box_height)
            if not 120 <= box_width <= 240 or not 48 <= box_height <= 72 or fill < 0.70:
                continue
            boxes.append(
                ButtonBox(
                    left + x,
                    top + y,
                    left + x + box_width - 1,
                    top + y + box_height - 1,
                    palette,  # type: ignore[arg-type]
                )
            )
    return sorted(boxes, key=lambda box: box.left)


def _button_text_mask(frame: np.ndarray, box: ButtonBox) -> np.ndarray | None:
    inset_x = max(4, round(box.width * 0.05))
    inset_y = max(4, round(box.height * 0.14))
    crop = frame[
        box.top + inset_y : box.bottom - inset_y + 1,
        box.left + inset_x : box.right - inset_x + 1,
    ]
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    # Blue buttons have pale antialiased edges.  A tighter saturation ceiling
    # keeps those edges from joining the actual white glyphs.
    mask = cv2.inRange(hsv, (0, 0, 150), (179, 105, 255))
    count, labels, stats, _centroids = cv2.connectedComponentsWithStats(mask)
    kept = np.zeros_like(mask)
    for index in range(1, count):
        _x, _y, _width, component_height, area = (int(value) for value in stats[index])
        if area >= 6 and component_height >= max(5, round(crop.shape[0] * 0.12)):
            kept[labels == index] = 1
    ys, xs = np.nonzero(kept)
    if not len(xs):
        return None
    return kept[ys.min() : ys.max() + 1, xs.min() : xs.max() + 1]


def _template_distance(observed: np.ndarray, template: np.ndarray) -> float:
    observed_height, observed_width = observed.shape
    template_height, template_width = template.shape
    nominal_width = observed_height * template_width / template_height
    best = 1.0
    crop_widths = {
        min(observed_width, max(1, round(nominal_width * scale)))
        for scale in (0.90, 0.95, 1.0, 1.05, 1.10)
    }
    # Suffixes such as ``×2`` can be taller than the main word, so height
    # alone can overestimate the prefix width.  The bounded fraction search
    # still compares the rendered glyphs; it only chooses where that suffix
    # begins.
    crop_widths.update(
        round(observed_width * fraction) for fraction in (0.70, 0.75, 0.775, 0.80, 0.85)
    )
    for crop_width in crop_widths:
        if observed_width < nominal_width * 0.72:
            continue
        candidate = observed[:, :crop_width]
        normalized = cv2.resize(
            candidate,
            (template_width, template_height),
            interpolation=cv2.INTER_AREA,
        )
        binary = (normalized >= 0.5).astype(np.uint8)
        best = min(best, float(np.mean(np.abs(binary.astype(np.int8) - template))))
    return best
