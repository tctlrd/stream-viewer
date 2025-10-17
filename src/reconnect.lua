-- Configuration
local CHECK_INTERVAL = 3
local MAX_STALL_TIME = 6

-- State
local prev_pos = 0
local last_pos_time = 0

-- Log function with timestamp
local function log(level, message)
    local time = os.date("%Y-%m-%d %H:%M:%S")
    mp.msg[level](string.format("[%s] %s", time, message))
end

-- Check if the stream is making progress
local function check_stream()
    local time_pos = mp.get_property_number("time-pos")
    local current_time = mp.get_time()

    if time_pos == nil then
        log("error", "Stream not properly loaded yet.")
        return
    end

    if time_pos == prev_pos then
        if last_pos_time == 0 then
            last_pos_time = current_time
        elseif (current_time - last_pos_time) > MAX_STALL_TIME then
            log("warn", "Stream stalled, attempting to reload...")
            mp.commandv("loadfile", mp.get_property("path"), "replace")
            last_pos_time = 0  -- Reset the last position time
        end
    else
        prev_pos = time_pos
        last_pos_time = current_time
    end
end

-- Set up the periodic check
mp.add_periodic_timer(CHECK_INTERVAL, check_stream)

log("info", "IP Camera monitoring script loaded.")