package com.mkuiwu.douzero.runtime.domain;

/** 玩家在当前牌局中的阵营角色。 */
public enum Role {
    /** 地主阵营；一局中恰好一名玩家。 */
    LANDLORD,

    /** 农民阵营；两名农民共享胜负目标。 */
    FARMER
}
