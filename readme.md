
# WBAN Tool

This tool generates and analyzes sensor models based on a structured JSON input.  
It performs the following steps:

1.  Builds a **ProFeat model** from JSON.
    
2.  Translates it to **PRISM** using ProFeat (Docker).
    
3.  Optionally:
    
    -   Adds sensor-specific labels and rewards.
        
    -   Builds a **SWITSS ReachabilityForm** and computes witnessing subsystems.
        
4.  Runs **STORM** (Docker) for model checking.
    
5.  Parses results and exports:
    
    -   `results.json`
        
    -   Console report
        
    -   Pareto front plots (if applicable)
        

----------

# 1. Requirements

## 1.1 Docker

Docker must be installed and running.

Used Docker images:

-   `profeat`
    
-   `movesrwth/storm:stable`
    

You can change the Image names at the start of the file if necessary.


----------

## 1.2 PRISM (Local Installation Required for SWITSS)

PRISM must be installed locally because it is used to export the explicit model (`.tra/.lab`) for SWITSS.

You must set the correct path in the script:
``` PYTHON
PRISM_CMD  = [r"C:\Program Files\prism-4.9\bin\prism.bat"]
```
and add PRISM to your system PATH

Test your PRISM installation:

```prism -version```

If this fails, either:

-   check if you added PRISM correctly to your system PATH, or
    
-   Adjust `PRISM_CMD` in the script accordingly.
    

----------

## 1.3 SWITSS

SWITSS must be installed in your Python environment.

https://github.com/simonjantsch/switss

The WBAN tool must be placed inside the SWITSS directory so that imports like:

from  switss.model  import  MDP, ReachabilityForm

work correctly. (It needs to be in the same folder as ```__init__.py```)


----------

# 2. Configuration in the Script

At the top of the script, you must adjust:
```
WORKDIR_HOST_FS  
WORKDIR_DOCKER_MOUNT  
CONTAINER_DIR  
PROFEAT_DOCKER_IMAGE  
STORM_DOCKER_IMAGE  
PRISM_CMD
```

----------

# 3. Usage

Run the tool with:
```
python WBAN.py --input model.json
```
Optional arguments:

--profeat-name WBAN.profeat  
--ctl-name WBAN.ctl

Example:
```
python WBAN.py --input example_model.json --profeat-name example.profeat --prop-name example.ctl
```
----------

# 4. Input JSON Format

The input JSON must contain:
```JSON
{  
 "global_parameters": { ... },  
 "nodes": [ ... ],  
 "connections": [ ... ],  
 "queries": [ ... ],  
 "switss": { ... },              // optional  
 "export_scheduler": { ... }     // optional  
}
```
----------

## 4.1 global_parameters (optional)

Defines default values for all nodes and connections.

Example:
```JSON
"global_parameters": {  
 "buffer_size": 3,  
 "generate_prob": 0.5,  
 "connect_fail": 0.1  
}
```
If not specified, defaults are taken from `INIT_GLOBALS`.

----------

## 4.2 nodes (required)

Each node must contain:
```JSON
{  
 "name": "S0",  
 "type": "source"  
}
```
Allowed types:

-   `"source"`
    
-   `"relay"`
    
-   `"target"`
    

Optional per-node parameters override global defaults.

Example:
```JSON
{  
 "name": "R1",  
 "type": "relay",  
 "buffer_size": 5,  
 "energy_feature": true  
}
```
----------

## 4.3 connections (required)

Each connection:
```JSON
{  
 "sender": "S0",  
 "receiver": "R1"  
}
```
Optional connection parameters:
```JSON
{  
 "sender": "S0",  
 "receiver": "R1",  
 "connect_fail": 0.2  
}
```
----------

# 5. Queries

Queries are translated into STORM properties (`.ctl` file).

Supported types:

----------

## 5.1 Reachability Probability
```JSON
{  
 "name": "ReachBatteryEmpty",  
 "type": "reachability_probability",  
 "target": { "node": "S0", "predicate": "batt_empty" },  
 "opt": "max"  
}
```
Supported sensor predicates:

-   `"buffer_full"`
    
-   `"batt_empty"`
    
    
-   `"node_failed"`
    

----------

## 5.2 Bounded Reachability
```JSON
{  
 "type": "bounded_reachability_probability",  
 "target": { "node": "S0", "predicate": "batt_empty" },  
 "bound_steps": 10,  
 "opt": "min"  
}
```
----------

## 5.3 Expected Reward Until
```JSON
{  
 "type": "expected_reward_until",  
 "reward": { "name": "successful_transmissions", "node": "S0" },  
 "until": { "node": "S0", "predicate": "batt_empty" },  
 "opt": "max"  
}
```
----------

## 5.4 Long-Run Average Reward
```JSON
{  
 "type": "lra_reward",  
 "reward": { "name": "energy_used", "node": "S0" },  
 "opt": "min"  
}
```
----------

## 5.5 Pareto Optimization
```JSON
{  
 "type": "pareto",  
 "name": "Energy vs Transmissions",  
 "objectives": [  
 {  
 "type": "lra_reward",  
 "reward": { "name": "energy_used", "node": "S0" },  
 "opt": "min"  
 },  
 {  
 "type": "lra_reward",  
 "reward": { "name": "successful_transmissions" },  
 "opt": "max"  
 }  
 ]  
}
```
This produces:

-   Pareto points in `results.json`
    
-   PNG plots in the workspace
    

----------

# 6. SWITSS (Optional)

If the JSON contains:
```JSON
"switss": {
    "target": {
      "sensor_label": {
        "node": "R0",
        "kind": "buffer_full"
      }
    },
    "threshold": 0.2
  }
```
The tool:

1.  Exports explicit PRISM model (`.tra`, `.lab`)
    
2.  Builds `ReachabilityForm`
    
3.  Computes a witnessing subsystem
    
4.  Outputs:
    
    -   `witness_subsystem.json`
        
    -   `witness_subsystem.dot`
        
    -   `witness_subsystem.pdf`
        

----------

# 7.Scheduler

To export the scheduler use:
```JSON
"export_scheduler": {
    "path": "scheduler.json",
    "build_state_valuations": true,
    "build_choice_origins": true
  }
```
- set "build_state_valuations": true to include the full variable valuations for each state in the exported scheduler
- set "build_choice_origins": true to include the origin of each selected action in the exported scheduler

# 8. Outputs

All outputs are written to:

WORKDIR_HOST_FS

Generated files:

-   `WBAN.profeat`
    
-   `WBAN.prism`
    
-   `WBAN.ctl`
    
-   `results.json`
    
-   `pareto.png`

-  `scheduler.json` (if enabled)
    
-   `witness_subsystem.*` (if SWITSS enabled)