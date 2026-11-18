% Provenance: ATG-lineage source; original/local edit boundary is unknown.
% Retain original author notices. See UPSTREAM.md and ../../THIRD_PARTY.md.
%
function [signames,X,t] = smt_encode_and_check(stlobject,mT,k,signal_pref,delta,check_negation)
% SMT_ENCODE_AND_CHECK(mT,k,signal_pref,delta) takes an STL object and 
% several parameters and produces a signal that satisfies the formula.
%
% Inputs:
%
% stlobject - The STL requirement object
%
% mT - Time horizon within which the switchings in the signal need to occur
%
% k - Input corresponding to the number of switchings in the signal
%
% signal_pref - Interpretation of the resulting signal. 0 means 
%               'piecewise constant'; 1 means 'piecewise linear'
%
% delta - Difference between signal values allowed at the point of 
%         switching
%
% check_negation - Flag that determines whether an example of the property 
%                  is checked or the negation of the property.
%
% The output SIGNAMES is a cell array of strings that corresponds to each 
% signal; the jth element of SIGNAMES corresponds to the jth row of X.
%
%
% J. Kapinski
% 6-2017
%

T = STL_Parse_Table(stlobject);

n = size(T);


fid = fopen('stl_translation.smt2', 'w');
smt_time(k, mT, fid);
smt_initialize_signals(stlobject, T, k, signal_pref, delta, fid);
for i = 1:n(1)
    j = n(1) - i + 1;
    switch T{j,2}
    case 'or'
        smt_or(j, T{j,3}, T{j,4}, k, fid);
    case 'and'
        smt_and(j, T{j,3}, T{j,4}, k, fid);
    case '=>'
        smt_implies(j, T{j,3}, T{j,4}, k, fid);
    case 'not'
        smt_not(j, T{j,3}, k, fid);
    case 'always'
       smt_always(j, T{j,3}, k, T{j,5}, T{j,6}, fid);
    case 'eventually'
       smt_eventually(j, T{j,3}, k, T{j,5}, T{j,6}, fid);
    case 'until'
        smt_until(j, T{j,3}, T{j,4}, k, T{j,5}, T{j,6}, fid);
    case 'predicate'
        smt_predicate(j, k, T{j,1}, signal_pref, fid);
    otherwise
        disp('other value')
    end
end

fprintf(fid, '\n\n ; FINAL FORMULA SATISFACTION \n');
if check_negation
    fprintf(fid,'(assert  (not b_1_1))\n');
else
    fprintf(fid,'(assert  b_1_1)\n');
end
fprintf(fid,'(check-sat)\n');
fprintf(fid,'(get-model)\n');
fid = fclose(fid);
global ATG_Z3_LAST_SECONDS
z3_timer = tic;
system('z3 -smt2 stl_translation.smt2 > model.txt');
ATG_Z3_LAST_SECONDS = toc(z3_timer);

[signames,X,t] = SMTModel2Arrays(stlobject,'model.txt');

end
