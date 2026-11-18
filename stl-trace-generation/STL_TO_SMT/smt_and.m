% Provenance: ATG-lineage source; original/local edit boundary is unknown.
% Retain original author notices. See UPSTREAM.md and ../../THIRD_PARTY.md.
%
function smt_and(i, i1, i2, k, fid)
 fprintf(fid, '\n\n ; AND \n');
    for j = 1:k
       fprintf(fid, '(declare-fun b_%d_%d () Bool)\n',i,j);
       fprintf(fid, '(assert (= b_%d_%d (and b_%d_%d b_%d_%d)))\n', i, j, i1, j, i2, j);    
    end
