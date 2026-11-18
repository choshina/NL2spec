% Provenance: ATG-lineage source; original/local edit boundary is unknown.
% Retain original author notices. See UPSTREAM.md and ../../THIRD_PARTY.md.
%
function smt_always(i, i1, k, t1, t2, fid)
    fprintf(fid, '\n\n ; ALWAYS \n');
    for j = 1:k
        fprintf(fid, '(declare-fun b_%d_%d () Bool)\n',i,j);
        fprintf(fid, '(declare-fun a_%d_%d () Real)\n',i,j);
        if j == 1
            fprintf(fid, '(assert (= a_%d_%d 0))\n',i,j);
        else
            % Intializing auxiliary variables
            fprintf(fid, '(assert (=> (= b_%d_%d true) (= a_%d_%d (+ a_%d_%d (- t_%d t_%d)))))\n', i1,j-1,i,j,i,j-1, j, j-1);
            fprintf(fid, '(assert (=> (= b_%d_%d false) (= a_%d_%d 0)))\n', i1,j-1,i,j);
        end
    end
    % Case box psi is true
    for j = 1:k
         fprintf(fid, '(assert (=> (= b_%d_%d true) (or false \n', i, j);
         if j < k
            fprintf(fid, '(and (< t_%d (+ t_%d %f)) (= b_%d_%d true))\n', k, j, t1, i1, k);
            if t2 == inf
                fprintf(fid, '(and (<= (+ t_%d %f) t_%d) (<= (- t_%d (+ t_%d %f)) a_%d_%d) (= b_%d_%d true) )\n',j, t1, k, k, j, t1, i, k, i1, k);
            else
                fprintf(fid, '(and (<= (+ t_%d %f) t_%d) (<= t_%d (+ t_%d %f)) (<= (- t_%d (+ t_%d %f)) a_%d_%d) (= b_%d_%d true) )\n',j, t1, k, k, j+1, t2, k, j, t1, i, k, i1, k);
            end
            if t2 ~= inf
                fprintf(fid, '(or false false \n');
                for l = 1:k-1
                    fprintf(fid, '(and (< t_%d  (+ t_%d %f) ) (<= (+ t_%d %f) t_%d) (<= (-  t_%d (+ t_%d %f)) a_%d_%d) )\n',l, j+1, t2, j+1, t2, l+1, l+1, j, t1, i, l+1);    
                end
                fprintf(fid, '\n ) ');
            end      
        else
             fprintf(fid, '(= b_%d_%d true)\n', i1, k);
        end
        fprintf(fid, ')))\n');
    end
    
    %Case box psi is false
    for j = 1:k
         fprintf(fid, '(assert (=> (= b_%d_%d false) (and true true ', i, j);
         if j < k
            if t2 == inf
               fprintf(fid, '(or false (and (< t_%d (+ t_%d %f)) (= b_%d_%d false)) ', k, j+1, t1, k, i1);
               for l=1:k-1
                    fprintf(fid, '(and (<= (+ t_%d %f) t_%d) (or (= b_%d_%d false) (<= (- t_%d (+ t_%d %f)) (a_%d_%d))))', j+1, t1, l, i1, l, l, j+1, t1, i, l);
               end
               fprintf(fid, ')');
            else
                fprintf(fid, '(=> (< t_%d (+ t_%d %f)) (= b_%d_%d false))', k, j, t1, i1, k);
                for l = 1:k-1
                    fprintf(fid, '(=> (and (<= (+ t_%d %f) %f) (<= t_%d (+ t_%d %f)) )   (<= a_%d_%d %f))', j, t2, l, l, j+1, t2, i, l, t2-t1);
                    fprintf(fid, '(=> (and (< t_%d (+ t_%d %f)) (<= (+ t_%d %f) t_%d)) (<= (- a_%d_%d (- t_%d (+ t_%d %f))) %f))', l, j+1, t2, j+1, t2, l+1, i, l+1, l+1, j+1, t2, t2-t1);
                end
                fprintf(fid, '(=> (and (<= (+ t_%d %f) t_%d) (< t_%d (+ t_%d %f))) (or (= b_%d_%d false) (<= (+ (- (+ t_%d %f) t_%d) a_%d_%d) %f)) )', j, t1, k, k, j+1, t2, i1, k, j+1, t2, k, i, k, t2-t1);
            end 
        else
             fprintf(fid, '(= b_%d_%d false)', i1, k);
        end
        fprintf(fid, ')))\n');
     end
