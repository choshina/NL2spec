% Provenance: ATG-lineage source; original/local edit boundary is unknown.
% Retain original author notices. See UPSTREAM.md and ../../THIRD_PARTY.md.
%
function smt_eventually(i, i1, k, t1, t2, fid)
    fprintf(fid, '\n\n ; EVENTUALLY \n');
    smt_not(i1+1, i1+2, k, fid);
    smt_always(i1, i1+1, k, t1, t2, fid);    
    smt_not(i, i1, k, fid);
