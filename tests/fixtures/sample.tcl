# sample fixture: comments, nesting, loops, procs, expansion
set MODE func            ;# trailing comment
set PERIOD 10.0
set x a#b

create_clock -name clk -period $PERIOD [get_ports clk]

set_clock_groups -asynchronous \
    -group {clk_a clk_b} \
    -group {clk_c}

proc sign_extend {v bits} {
    set top [expr {1 << ($bits - 1)}]
    if {$v >= $top} {
        set v [expr {$v - (1 << $bits)}]
    }
    return $v
}

if {$MODE == "func"} {
    set_false_path -from [get_ports rst_n]
} elseif {$MODE == "scan"} {
    set_false_path -from [get_ports scan_en]
} else {
    puts "unknown mode"
}

foreach clk {clk_a clk_b clk_c} {
    for {set i 0} {$i < 3} {incr i} {
        while {$i < 2} {
            puts "$clk $i"
        }
    }
}

switch -exact -- $MODE {
    func { puts "functional" }
    scan { puts "scan" }
    default { puts "other" }
}

if {$MODE == "func"} {
    if {$PERIOD > 5} {
        set_clock_uncertainty 0.1 [get_clocks clk]
    }
}
