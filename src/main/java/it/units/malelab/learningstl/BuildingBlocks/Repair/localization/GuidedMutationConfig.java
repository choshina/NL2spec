/*
 * Provenance: STL Repair addition; not present in the BUSTLE reference snapshot.
 * BUSTLE comparison reference: e945ad648036a05d788fe72f92b1ce6a41ddd98b.
 * This file builds on BUSTLE-derived interfaces but is a repository-specific extension.
 * See the package PROVENANCE.md and repository THIRD_PARTY.md.
 */
package it.units.malelab.learningstl.BuildingBlocks.Repair.localization;

public final class GuidedMutationConfig {

    public final String localizationMethod;

    public final int initialTopK;
    /** rho: when > 0 the gate keeps the top max(initialTopK, ceil(rho * |V_phi|)) ranked nodes (ties retained) of the current hypothesis. */
    public final double topKRatio;
    public final int expansionStep;
    public final int stagnationWindow;
    public final double ucbBeta;
    public final int selfCoefficientCap;
    public final double evidenceWeight;
    public final double museWeight;
    public final double sizeWeight;
    public final double brokenLambda;
    public final double rewardWeight;
    public final double rewardClip;
    public final double fitnessGainWeight;
    /**
     * Type-priority trial: on a gate node that earned self credit (temporal credit c > 1, or atom credit c = K)
     * the diagnosed-type edits are tried first; if one of them fits D exactly the node's other edits are skipped.
     */
    public final boolean typePriority;

    public GuidedMutationConfig(
            String localizationMethod,
            int initialTopK,
            int expansionStep,
            int stagnationWindow,
            double ucbBeta,
            int selfCoefficientCap,
            double evidenceWeight,
            double museWeight,
            double sizeWeight,
            double brokenLambda,
            double rewardWeight,
            double rewardClip,
            double fitnessGainWeight,
            double topKRatio,
            boolean typePriority
    ) {
        this.typePriority = typePriority;
        this.localizationMethod = localizationMethod == null ? "sgrl" : localizationMethod.toLowerCase();
        this.initialTopK = Math.max(1, initialTopK);
        this.topKRatio = Math.max(0.0, topKRatio);
        this.expansionStep = Math.max(1, expansionStep);
        this.stagnationWindow = Math.max(1, stagnationWindow);
        this.ucbBeta = Math.max(0.0, ucbBeta);
        this.selfCoefficientCap = Math.max(1, selfCoefficientCap);
        this.evidenceWeight = Math.max(0.0, evidenceWeight);
        this.museWeight = Math.max(0.0, museWeight);
        this.sizeWeight = Math.max(0.0, sizeWeight);
        this.brokenLambda = Math.max(0.0, brokenLambda);
        this.rewardWeight = Math.max(0.0, rewardWeight);
        this.rewardClip = Math.max(0.0, rewardClip);
        this.fitnessGainWeight = Math.max(0.0, fitnessGainWeight);
    }
}
