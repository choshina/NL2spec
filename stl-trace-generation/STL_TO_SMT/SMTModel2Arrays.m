% Provenance: ATG-lineage source; original/local edit boundary is unknown.
% Retain original author notices. See UPSTREAM.md and ../../THIRD_PARTY.md.
%
function [signames,X,t] = SMTModel2Arrays(STLObj,MODELFILENAME)
% SMTModel2Arrays(STLOBJ,MODELFILENAME) processes the model output from an
% SMT solver like Z3 and outputs the model into MATLAB arrays. The function
% assumes that the SMT model output file is within the same folder and is
% named MODELFILENAME. The function also assumes that there are a
% collection of real variables named signame_t_i, where 'signame' is the
% name of one of the signals used to define STLOBJ, and a collection of
% real variables named t_i. The signame_t_i variable values are mapped into
% a MATLAB array X, where each X(j,i) = val(signamej_t_i), where val(y) is
% the valuation of the variable y. The output SIGNAMES is a cell array of
% strings that corresponds to each signal; the jth element of SIGNAMES
% corresponds to the jth row of X.
% The t_i variables are mapped into a MATLAB array t, where each
% t(i) = val(t_i).
%
% Example usage:
%
% [b,t] = SMTModel2Arrays()
%
% J. Kapinski
% 6-2017
%

signames =  STL_ExtractSignals(STLObj);
X = [];
t = [];

FID = fopen(MODELFILENAME,'r');

% Get the first line
TLINE = fgetl(FID);

while TLINE~=-1
    % Detect whether this line begins the definition of a variable
    variable_test = regexp(TLINE,'(define-fun');
    if ~isempty(variable_test)
        % Detect whether this is a signal variable. Note that we ignore
        % any variables associated with time shifted versions of signals,
        % as they are essentially providing redundant information. We
        % assume that the signal name ends in "_dt_num", where "num" is
        % some number.
        sig_var_test = regexp(TLINE,'\s\w*_t_\d+\s');
        time_var_test = regexp(TLINE,'\st_\d+\s');
        if ~isempty(sig_var_test)
            % This line begins the definition of a signal variable
            % of interest
            
            signal_name = regexp(TLINE,'\s(\w*)_t_\d+\s','tokens');
            signal_time_index = regexp(TLINE,'\s\w*_t_(\d+)\s','tokens');
            signal_time_index = eval(signal_time_index{1}{1});
            
            % First, find the index of this signal
            [~,sigindex] = ismember(signal_name{1}{1},signames);
            if sigindex==0
                error('Signal name does not appear in the list of signals.');
            else
                % Get the next line
                TLINE = fgetl(FID);
                value_out = parse_SMT_model_valuation(TLINE);
                X(sigindex,signal_time_index) = value_out;
            end
        elseif ~isempty(time_var_test)
            % This line begins the definition of a time variable of
            % interest
            time_var_indices = regexp(TLINE,'t_(\d+)','tokens');
            % If the variable is in the expected form t_i, then the first
            % element of time_var_indices will be a 1x1 cell array.
            size_of_time_labels = size(time_var_indices{1});
            if ~(size_of_time_labels(1)==1&&size_of_time_labels(2)==1)
                error('Unexpected time variable label.');
            else
                i_val = eval(time_var_indices{1}{1});
                % Get the next line and assume that it contains the
                % valuation of the real value.
                TLINE = fgetl(FID);
                value_out = parse_SMT_model_valuation(TLINE);
                t(i_val) = value_out;
            end
        end
    end
    
    % Get the next line
    TLINE = fgetl(FID);
    
end

% Close the file
ST = fclose(FID);
end
