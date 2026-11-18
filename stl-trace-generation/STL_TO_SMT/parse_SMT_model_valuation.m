% Provenance: ATG-lineage source; original/local edit boundary is unknown.
% Retain original author notices. See UPSTREAM.md and ../../THIRD_PARTY.md.
%
function value_out = parse_SMT_model_valuation(TLINE)
% This function takes a str representing a line from the result of the SMT
% file and returns a number. It is assumed that this line corresponds to a
% variable valuation and is given in one of 3 forms:
% 1. An integer value
% 2. A rational number
% 3. A polynomial root: This case is not yet handled.
%

% First, determine whether the model returned by Z3 is
% given as the solution to a polynomial.
poly_root_test = regexp(TLINE,'(root-obj)');
% Next, check whether the model returned by Z3 is given in
% terms of a rational. Assume this is the case when there
% is a '\' in this line.
rational_test = regexp(TLINE,'(/)');
% Lastly, do a sanity check to see whether, if the above 2
% cases are not true, that there is at least a number given
% in this line.
number_test = regexp(TLINE,'(\d*\.?\d*)');
if ~isempty(poly_root_test)
    error('Polynomial roots not yet supported.');
elseif ~isempty(rational_test)
    % Determine whether there is a negative sign present.
    % Assume that, if so, the negative sign negates the
    % entire rational. If there is more than one sign
    % present, then this is some unexpected situation.
    negation_test = regexp(TLINE,'-');
    if length(negation_test)>1
        error('Unexpected number of negation signs.');
    elseif length(negation_test)==1
        negation_str = '-';
    else
        negation_str = '';
    end
    
    % Find the numerator and the denominator
    rational_arguments = regexp(TLINE,'(\d*\.?\d*)','tokens');
    value_out = eval([negation_str rational_arguments{1}{1} '/' rational_arguments{2}{1}]);
elseif ~isempty(number_test)
    % Determine whether there is a negative sign present.
    negation_test = regexp(TLINE,'-');
    if length(negation_test)>1
        error('Unexpected number of negation signs.');
    elseif length(negation_test)==1
        negation_str = '-';
    else
        negation_str = '';
    end
    
    % Find the value
    value_str = regexp(TLINE,'(\d*\.?\d*)','tokens');
    value_out = eval([negation_str value_str{1}{1}]);
else
    error('Unexpected variable value.');
end

end
