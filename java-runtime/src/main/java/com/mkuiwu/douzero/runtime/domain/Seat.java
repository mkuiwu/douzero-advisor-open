package com.mkuiwu.douzero.runtime.domain;

/** 牌局中的相对座位；地主确定后，另外两个座位仍保持稳定身份。 */
public enum Seat {
    /** 地主所在座位，也是每轮首先行动的基准座位。 */
    LANDLORD,

    /** 地主的下家，按正常出牌顺序在地主之后行动。 */
    LANDLORD_DOWN,

    /** 地主的上家，按正常出牌顺序在地主下家之后行动。 */
    LANDLORD_UP;

    /** 返回正常出牌顺序中的下一个座位。 */
    public Seat next() {
        return switch (this) {
            case LANDLORD -> LANDLORD_DOWN;
            case LANDLORD_DOWN -> LANDLORD_UP;
            case LANDLORD_UP -> LANDLORD;
        };
    }

    /** 根据现有座位派生阵营，避免身份出现两套来源。 */
    public Role role() {
        return this == LANDLORD ? Role.LANDLORD : Role.FARMER;
    }
}
