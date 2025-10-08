#!/bin/bash
# Save as ~/.config/sway/arrange_grid.sh

# Focus the workspace
swaymsg "workspace 1"

# Set layout to vertical split
swaymsg "layout splitv"

# Get window IDs
windows=($(swaymsg -t get_tree | jq -r '.. | select(.app_id? == "mpv") | .id'))

# First vertical split (top and bottom)
if [ ${#windows[@]} -ge 2 ]; then
    swaymsg "[con_id=${windows[0]}] split h"
    swaymsg "[con_id=${windows[1]}] split h"
fi

# Second vertical split (if we have 4 windows)
if [ ${#windows[@]} -ge 4 ]; then
    swaymsg "[con_id=${windows[0]}] focus"
    swaymsg "focus down"
    swaymsg "split h"
    swaymsg "[con_id=${windows[2]}] split h"
    swaymsg "[con_id=${windows[3]}] split h"
fi

# Make all windows equal size
swaymsg "focus parent; split toggle split; focus parent; split toggle split"