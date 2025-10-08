#!/bin/bash

# Get all window IDs with app_id="mpv"
windows=($(swaymsg -t get_tree | jq -r '.. | select(.app_id? == "mpv") | .id' | tr '\n' ' '))

# Check if we found any windows
if [ ${#windows[@]} -eq 0 ]; then
    echo "No MPV windows found!"
    exit 1
fi

echo "Found ${#windows[@]} MPV windows: ${windows[@]}"

# Focus workspace 1 and set layout to splitv
swaymsg "workspace 1; layout splitv"

# For each window, focus it and split it
for ((i=0; i<${#windows[@]}; i++)); do
    if [ $i -gt 0 ]; then
        # Split the container after the first window
        swaymsg "[con_id=${windows[$i]}] focus; split h"
    fi
done

# If we have more than 2 windows, create a second row
if [ ${#windows[@]} -gt 2 ]; then
    # Focus the first window and split down
    swaymsg "[con_id=${windows[0]}] focus; splitv"
    
    # For remaining windows in the second row
    for ((i=2; i<${#windows[@]}; i++)); do
        swaymsg "[con_id=${windows[$i]}] focus; split h"
    done
fi

# Make all splits equal
swaymsg "focus parent; toggle split; focus parent; toggle split"