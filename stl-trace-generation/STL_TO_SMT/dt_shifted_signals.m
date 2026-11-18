% Provenance: ATG-lineage source; original/local edit boundary is unknown.
% Retain original author notices. See UPSTREAM.md and ../../THIRD_PARTY.md.
%
function [minus_shifted_signals,plus_shifted_signals] = dt_shifted_signals(inpstr)
% DT_SHIFTED_SIGNALS(INPSTR) takes a string representeing a predicate that
% is based on some collection of signals that may (or may not) be shifted
% by some amount of time 'dt' and returns the sets of signals that are
% either advanced or delayed by the 'dt' amount. It is assumed that a
% signal x, for example, is either unshift, in which case it looks like
% 'x[t]', or it is positive shifted, in which case it looks something like
% 'x[t+dt]', or it is negative shifted, in which case it looks something
% like 'x[t-dt]'. The returned variables 'minus_shifted_signals' and
% 'plus_shifted_signals' contain cell arrays of strings, where each cell
% array represents either advanced or delayed signals, respectively. It is
% assumed that the string representing the delay is 'dt'.
%
% Example usage:
%
% [minus_shifted_signals,plus_shifted_signals] = dt_shifted_signals('fqa_qafc_rq[t+dt]-fqa_qafc_rq[t]> .4 +x[t]')
%
% J. Kapinski
% 6-2017
%

minus_shifted_signals = {};
plus_shifted_signals ={};


% Extract the collection of signals, along with their
signals_with_time = regexp(inpstr,'(\w+\[[^\]]*\])','tokens');

for sigind = 1:length(signals_with_time)
    sig_time_str = signals_with_time{sigind}{1};
    % Determine whether this signal is plus or minus shifted by 'dt' (or
    % not shifted)
    % Only inspect the time-index expression.  Looking for "dt" in the
    % complete token misclassifies ordinary signal names such as
    % width_POV[t] as time-shifted signals.
    time_index = regexp(sig_time_str,'\[([^\]]*)\]','tokens','once');
    if isempty(time_index)
        continue;
    end
    time_index = time_index{1};
    shift_present = regexp(time_index,'\<dt\>','tokens');
    if ~isempty(shift_present)
        % There is some shift present
        % Detect whether this is a minus shift
        minus_shift_present = regexp(time_index,'(^|\s)-\s*dt\s*$','tokens');
        plus_shift_present = regexp(time_index,'(^|\s)\+\s*dt\s*$|^\s*dt\s*$','tokens');
        if ~isempty(minus_shift_present)
            % This is a -dt time shift
            this_shifted_signal = regexp(sig_time_str,'(\S+)\[','tokens');
            minus_shifted_signals{end+1} = this_shifted_signal{1}{1};
        elseif ~isempty(plus_shift_present)
            % This is a +dt time shift
            this_shifted_signal = regexp(sig_time_str,'(\S+)\[','tokens');
            plus_shifted_signals{end+1} = this_shifted_signal{1}{1};
        else
            error('Some unknown type of time shift is present.');
        end
    end
    
end
end
