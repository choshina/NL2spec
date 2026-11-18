% Provenance: ATG-lineage source; original/local edit boundary is unknown.
% Retain original author notices. See UPSTREAM.md and ../../THIRD_PARTY.md.
%
function [signames,X,t] = smt_add_blocking_and_check(STLObj,MODELFILENAME,SMTFILENAME,varargin)
% This function is part of an implementation of a tool to automatically
% generate traces that satisfy a given STL formula, using the Z3 SMT
% solver. This function adds blocking conditions to the existing SMT2 query
% so that a different model (satisfying assignment) is produced.
%
% The following optional inputs arguments are supported:
%
% 'RealValuedBlocking': When this option is present, a subsequent logical
%                       (true/false) argument should be supplied to
%                       indicate whether the blocking conditions should be
%                       included for the real-valued variables. Default is
%                       set to "true".
%
% 'BooleanValuedBlocking': When this option is present, a subsequent
%                          logical (true/false) argument should be supplied
%                          to indicate whether the blocking conditions
%                          should be included for the Boolean-valued
%                          variables. Default is set to "true".
%
% 'Delta': When this option is present, the subsequent input should be a
%          real-valued number that determines blocking condition "size" for
%          the real-valued variables. Note that this can be 0. Also, note
%          that the default value is "1".
%
% Example usage:
%
% J. Kapinski
% Toyota
% 7-2017
%

% Handle options
delta = 1;
BlockReals = true;
BlockBools = true;

if nargin>3
    remaining_args = nargin-3;
    while remaining_args>0
        this_arg_num = nargin-remaining_args-3+1;
        this_arg_str = varargin{this_arg_num};
        if ischar(this_arg_str)
            switch this_arg_str
                case 'RealValuedBlocking'
                    if remaining_args>1&&islogical(varargin{this_arg_num+1})
                        BlockReals = varargin{this_arg_num+1};
                        remaining_args = remaining_args - 2;
                    else
                        error('Incorrect number of arguments.');
                    end
                case 'BooleanValuedBlocking'
                    if remaining_args>1&&islogical(varargin{this_arg_num+1})
                        BlockBools = varargin{this_arg_num+1};
                        remaining_args = remaining_args - 2;
                    else
                        error('Incorrect number of arguments.');
                    end
                case 'Delta'
                    if remaining_args>1&&isnumeric(varargin{this_arg_num+1})
                        delta = varargin{this_arg_num+1};
                        remaining_args = remaining_args - 2;
                    else
                        error('Incorrect number of arguments.');
                    end
                otherwise
                    error('Unexpected input.');
            end
        else
            error('Unexpected input argument.');
        end
    end
end

% First, read the current SMT2 model file and determine what the signal
% assignements are for each time period. This will correspond to lines in
% the text file that contain variable names like ***_t_#

signames =  STL_ExtractSignals(STLObj);
X = [];
t = [];

FID = fopen(MODELFILENAME,'r');

% Get the first line
TLINE = fgetl(FID);

additional_assertions = '';
real_assertions = '';
boolean_assertions = '';

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
        bool_var_test = regexp(TLINE,'\sb_\d+_\d+\s');
        time_var_test = regexp(TLINE,'\st_\d+\s');
        if ~isempty(sig_var_test)&&BlockReals
            % This line begins the definition of a real-valued signal 
            % variable of interest
            signal_name = regexp(TLINE,'\s(\w*_t_\d+)\s','tokens');
            signal_name = signal_name{1}{1};
            if delta==0
                % There is no "size" to the blocking condition
                real_assertions = [real_assertions '(= ' signal_name ' ' ];
                % Get the next line
                TLINE = fgetl(FID);
                real_assertions = [real_assertions TLINE ' '];
            else
                % Impose a nonzero "size" to the blocking condition
                % Get the next line
                TLINE = fgetl(FID);
                % Get the value indicated in the next line of the model
                % file
                value_out = parse_SMT_model_valuation(TLINE);
                % Define the blocking condition
                value_text = format_smt_decimal(value_out);
                delta_text = format_smt_decimal(delta);
                real_assertions = [real_assertions '(< ' signal_name ' (+ ' value_text ' ' delta_text ')) (> ' signal_name ' (- ' value_text ' ' delta_text ')) \n'];
            end
        elseif ~isempty(bool_var_test)&&BlockBools
            % This line begins the definition of a Boolean variable
            % of interest
            signal_name = regexp(TLINE,'\s(b_\d+_\d+)\s','tokens');
            signal_name = signal_name{1}{1};
            % Get Boolean value
            TLINE = fgetl(FID);
            value_out = regexp(lower(TLINE),'(true|false)','tokens');
            value_out = value_out{1}{1};
            % Add Boolean assertion
            boolean_assertions = [boolean_assertions '(= ' signal_name ' ' value_out ') ' ];
        elseif ~isempty(time_var_test)
            % Do nothing for now
        end
    end
    
    % Get the next line
    TLINE = fgetl(FID);
    
end

if ~isempty(boolean_assertions)
    boolean_assertions = ['(assert (not (and ' boolean_assertions ')))\n'];
    additional_assertions = [additional_assertions boolean_assertions];
end
if ~isempty(real_assertions)
    real_assertions = ['(assert (not (and ' real_assertions ')))\n'];
    additional_assertions = [additional_assertions real_assertions];
end

% Close the model file
ST = fclose(FID);

FID = fopen(SMTFILENAME,'r+');
% fseek(FID,0,-1);
% Get the first line
TLINE = fgetl(FID);
temp_file_str = [TLINE '\n'];

while ~isequal(TLINE,-1)
    % Detect whethe
    final_smt_section_test = regexp(TLINE,'FINAL FORMULA SATISFACTION');
    if ~isempty(final_smt_section_test)
        temp_file_str = [temp_file_str '; ADDITIONAL BLOCKING CONDITIONS \n' additional_assertions '\n'];
        temp_file_str = [temp_file_str TLINE '\n'];
        % Get the next line
        TLINE = fgetl(FID);
    else
        temp_file_str = [temp_file_str TLINE '\n'];
        % Get the next line
        TLINE = fgetl(FID);
    end
end

% Close the query file
ST = fclose(FID);

% Now, reopen and write the updated SMT2 query out
FID = fopen(SMTFILENAME,'w');

COUNT = fprintf(FID,temp_file_str);

% Close the query file
ST = fclose(FID);

global ATG_Z3_LAST_SECONDS
z3_timer = tic;
system('z3 -smt2 stl_translation.smt2 > model.txt');
ATG_Z3_LAST_SECONDS = toc(z3_timer);

[signames,X,t] = SMTModel2Arrays(STLObj,'model.txt');

end

function text = format_smt_decimal(value)
    text = sprintf('%.17f', value);
    text = regexprep(text, '0+$', '');
    text = regexprep(text, '\.$', '');
    if isempty(text) || strcmp(text, '-0')
        text = '0';
    end
end
