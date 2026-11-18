classdef FalsificationProblem < BreachProblem
    %  FalsificationProblem A class dedicated to falsification of STL formulas.
    %
    %  FalsificationProblem Properties
    %    BrSet_False -  BreachSet updated with falsifying parameter vectors
    %                   and traces whenever some are found 
    %    X_false     -  parameter values found falsifying the formula
    %    StopAtFalse - (default: true) if true, will stop as soon as a falsifying 
    %                   parameter is found. 
    % 
    %  FalsificationProblem Methods
    %    GetBrSet_False - returns BrSet_False 
    %
    % See also BreachProblem
    
    properties
        BrSet_False
        X_false
        StopAtFalse=true
    end
    
    methods
        
        % Constructor calls parent constructor
        function this = FalsificationProblem(BrSys, phi, params, ranges)
            switch nargin
                case 2
                    super_args{1} = BrSys;
                    super_args{2} = phi;
                case 3
                    super_args{1} = BrSys;
                    super_args{2} = phi;
                    super_args{3} = params;
                case 4
                    super_args{1} = BrSys;
                    super_args{2} = phi;
                    super_args{3} = params;
                    super_args{4} = ranges;
            end
            this = this@BreachProblem(super_args{:});
        end
        
        function ResetObjective(this)
            ResetObjective@BreachProblem(this);
            this.X_false = [];
            this.BrSet_False = [];
        end
        
        % Nothing fancy - calls parent solve then display falsifying params
        % if found.
        function [Xfalse, res] = solve(this)
            res = solve@BreachProblem(this);
            Xfalse = this.X_false;
        end
        
        % Logging
        function LogX(this, x, fval)
            if size(x,2)>1
                x = x';
            end 
  
            % Logging default stuff
            this.LogX@BreachProblem(x, fval);

            %  Logging falsifying parameters found
            if fval < 0
                this.X_false = [this.X_false x];
                if isempty(this.BrSet_False)
                    this.BrSet_False = this.BrSys.copy();
                else
                    this.BrSet_False.Concat(this.BrSys);
                end
                
                if this.StopAtFalse == true
                    this.stopping = true;
                end
            end
           
        end

        function BrFalse = GetBrSet_False(this)
            BrFalse = this.BrSet_False;
        end

        function DispResultMsg(this)
            if ~isempty(this.X_false)
                fprintf('\n ---- Falsified with obj=%g at \n', this.obj_log(end));
                param_values = this.X_false(:,1);
                for ip = 1:numel(this.params)
                    fprintf( '        %s = %g\n', this.params{ip},param_values(ip))
                end
                fprintf('\n');
            else 
                fprintf('\nNo falsifying trace found.\n');
                this.DispResultMsg@BreachProblem();
            end
        end
        
    end
end