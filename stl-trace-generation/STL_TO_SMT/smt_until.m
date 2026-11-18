% Provenance: ATG-lineage source; original/local edit boundary is unknown.
% Retain original author notices. See UPSTREAM.md and ../../THIRD_PARTY.md.
%
function smt_until(i, i1, i2, k, t1, t2, fid)
    fprintf(fid, '\n\n ; UNTIL \n');
    if t1 == 0 & t2 == inf
        smt_untimed_until(i, i2-1, i2, k, fid);
    elseif t1 > 0 & t2 == inf
        % varphi_1 U_[t1, inf) varphi_2 = box [0, t1] varphi_1 and box [t1, t1] (varphi_1 U varphi_2)
        % (i1) 1. box [0, t1] varphi_1 
        % 2. (varphi_1 U varphi_2)
        % 3. box [t1, t1] (varphi_1 U varphi_2)
        % 8. varphi_1
        % (i2) 9. varphi_2
        smt_always(i1, i2-1, k, 0, t1, fid);
        smt_untimed_until(i1+1, i2-1, i2, k, fid);
        smt_always(i1+2, i1+1, k, t1, t1, fid);
        smt_and(i, i1, i1+2, k, fid); 
    else
        % varphi_1 U_[t1, inf) varphi_2 = box [0, t1] varphi_1 and box [t1,
        % t1] (varphi_1 U varphi_2) and neg box [t1, t2] neg varphi_2
        % (i1) 1. box [0, t1] varphi_1 
        % 2. (varphi_1 U varphi_2)
        % 3. box [t1, t1] (varphi_1 U varphi_2)
        % 4. neg varphi_2
        % 5. box [t1, t2] neg varphi_2
        % 6. neg box [t1, t2] neg varphi_2
        % 7. box [0, t1] varphi_1 and box [t1, t1] (varphi_1 U varphi_2)
        % 8. varphi_1
        % (i2) 9. varphi_2
        smt_always(i1, i2-1, k, 0, t1, fid);
        smt_untimed_until(i1+1,i2-1, i2, k, fid);
        smt_always(i1+2, i1+1, k, t1, t1, fid);
        smt_not(i1+3, i2, k, fid);
        smt_always(i1+4, i1+3,k, t1, t2, fid);
        smt_not(i1+5, i1+4, k,fid);
        smt_and(i1+6, i1, i1+2, k, fid);
        smt_and(i, i1+6, i1+5, k, fid);
    end
