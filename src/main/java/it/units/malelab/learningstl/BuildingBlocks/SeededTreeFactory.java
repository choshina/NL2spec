/*
 * Provenance: STL Repair addition; not present in the BUSTLE reference snapshot.
 * BUSTLE comparison reference: e945ad648036a05d788fe72f92b1ce6a41ddd98b.
 * This file builds on BUSTLE-derived interfaces but is a repository-specific extension.
 * See the package PROVENANCE.md and repository THIRD_PARTY.md.
 */
package it.units.malelab.learningstl.BuildingBlocks;

import it.units.malelab.jgea.core.Factory;
import it.units.malelab.jgea.representation.tree.Tree;

import java.util.ArrayList;
import java.util.List;
import java.util.Random;

public class SeededTreeFactory implements Factory<Tree<String>> {

    private final List<Tree<String>> seeds;
    private final Factory<? extends Tree<String>> fallback;
    private final boolean copiesOnly;

    public SeededTreeFactory(List<Tree<String>> seeds, Factory<? extends Tree<String>> fallback, boolean copiesOnly) {
        this.seeds = seeds;
        this.fallback = fallback;
        this.copiesOnly = copiesOnly;
    }

    @Override
    public List<Tree<String>> build(int n, Random random) {
        List<Tree<String>> trees = new ArrayList<>();
        if (seeds.isEmpty()) {
            trees.addAll(fallback.build(n, random));
            return trees;
        }
        if (copiesOnly) {
            for (int i = 0; i < n; i++) {
                trees.add(Tree.copyOf(seeds.get(i % seeds.size())));
            }
            return trees;
        }
        for (Tree<String> seed : seeds) {
            if (trees.size() >= n) {
                return trees;
            }
            trees.add(Tree.copyOf(seed));
        }
        trees.addAll(fallback.build(n - trees.size(), random));
        return trees;
    }
}
