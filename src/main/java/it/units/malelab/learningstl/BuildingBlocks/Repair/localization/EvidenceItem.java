/*
 * Provenance: STL Repair addition; not present in the BUSTLE reference snapshot.
 * BUSTLE comparison reference: e945ad648036a05d788fe72f92b1ce6a41ddd98b.
 * This file builds on BUSTLE-derived interfaces but is a repository-specific extension.
 * See the package PROVENANCE.md and repository THIRD_PARTY.md.
 */
package it.units.malelab.learningstl.BuildingBlocks.Repair.localization;

public final class EvidenceItem {
    public final String nodePath;
    public final String nodeType;
    public final double time;
    public final double weight;
    public final double siteResponsibility;
    public final double selfCoefficient;
    public final double rawNodeContribution;
    public final boolean targetTruth;
    public final boolean actualTruth;
    public final String symptom;

    public EvidenceItem(String nodePath, String nodeType, double time, double weight, String symptom) {
        this(nodePath, nodeType, time, weight, weight, 1.0, weight, false, false, symptom);
    }

    public EvidenceItem(
            String nodePath,
            String nodeType,
            double time,
            double weight,
            double siteResponsibility,
            double selfCoefficient,
            double rawNodeContribution,
            boolean targetTruth,
            boolean actualTruth,
            String symptom
    ) {
        this.nodePath = nodePath;
        this.nodeType = nodeType;
        this.time = time;
        this.weight = weight;
        this.siteResponsibility = siteResponsibility;
        this.selfCoefficient = selfCoefficient;
        this.rawNodeContribution = rawNodeContribution;
        this.targetTruth = targetTruth;
        this.actualTruth = actualTruth;
        this.symptom = symptom;
    }
}
