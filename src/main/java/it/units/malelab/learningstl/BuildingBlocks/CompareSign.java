/*
 * Provenance: BUSTLE-derived file, modified in STL Repair.
 * BUSTLE comparison reference: e945ad648036a05d788fe72f92b1ce6a41ddd98b.
 * Original BUSTLE authorship is retained; local changes are identified in git history.
 * See the package PROVENANCE.md and repository THIRD_PARTY.md.
 */
package it.units.malelab.learningstl.BuildingBlocks;

import java.util.function.BiFunction;


public enum CompareSign {

    SMALLER("<", (Double a, Double b) -> b - a),
    GREATER(">", (Double a, Double b) -> a - b),
    SMALLER_OR_EQUAL("<=", (Double a, Double b) -> b - a),
    GREATER_OR_EQUAL(">=", (Double a, Double b) -> a - b),
    EQUAL("==", (Double a, Double b) -> - Math.abs(a - b));

    private final String sign;
    private final BiFunction<Double, Double, Double> function;

    CompareSign(String type, BiFunction<Double, Double, Double> func) {
        this.sign = type;
        this.function = func;
    }

    @Override
    public String toString() {
        return sign;
    }

    public BiFunction<Double, Double, Double> getValue() {
        return this.function;
    }

}
