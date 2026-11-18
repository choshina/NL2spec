% Provenance: ATG-lineage source; original/local edit boundary is unknown.
% Retain original author notices. See UPSTREAM.md and ../../THIRD_PARTY.md.
%
function smt_not(i, i1, k, fid)
    fprintf(fid, '\n\n ; NEGATION \n');
    for j = 1:k
        fprintf(fid, '(declare-fun b_%d_%d () Bool)\n',i,j);
        fprintf(fid, '(assert (= b_%d_%d (not b_%d_%d) ))\n', i, j, i1, j);    
    end
