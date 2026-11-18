/*
 * Provenance: STL Repair addition; not present in the BUSTLE reference snapshot.
 * BUSTLE comparison reference: e945ad648036a05d788fe72f92b1ce6a41ddd98b.
 * This file builds on BUSTLE-derived interfaces but is a repository-specific extension.
 * See the package PROVENANCE.md and repository THIRD_PARTY.md.
 */
package it.units.malelab.learningstl.BuildingBlocks.Repair.localization;

import it.units.malelab.jgea.representation.tree.Tree;

public final class RepairEdit {

    private final String nodePath;
    private final String family;
    private final String sourceNodeType;
    private final Tree<String> candidate;

    public RepairEdit(String nodePath, String family, String sourceNodeType, Tree<String> candidate) {
        this.nodePath = nodePath;
        this.family = family;
        this.sourceNodeType = sourceNodeType;
        this.candidate = Tree.copyOf(candidate);
    }

    public String getNodePath() {
        return nodePath;
    }

    public String getFamily() {
        return family;
    }

    public String getSourceNodeType() {
        return sourceNodeType;
    }

    public Tree<String> apply() {
        return Tree.copyOf(candidate);
    }

    public String key() {
        return nodePath + "|" + family;
    }
}
