-- Configuration
local CHECK_INTERVAL = 2      -- Check every 2 seconds
local MAX_STALL_TIME = 5      -- Consider stream stalled if no progress for 5 seconds
local MAX_RETRIES = 1000      -- Maximum number of reconnect attempts
local RETRY_DELAY = 2         -- Initial delay between retries in seconds

-- State
local prev_pos = 0
local last_pos_time = 0
local retry_count = 0
local is_reconnecting = false

-- Log function with timestamp
local function log(level, message)
    local time = os.date("%Y-%m-%d %H:%M:%S")
    mp.msg[level](string.format("[%s] %s", time, message))
end

-- Function to attempt reconnection
local function reconnect()
    if is_reconnecting then return end
    
    is_reconnecting = true
    local path = mp.get_property("path")
    
    if not path then
        log("error", "No path available for reconnection")
        is_reconnecting = false
        return
    end
    
    retry_count = retry_count + 1
    
    if retry_count > MAX_RETRIES then
        log("warn", string.format("Max reconnection attempts (%d) reached. Giving up.", MAX_RETRIES))
        is_reconnecting = false
        return
    end
    
    log("info", string.format("Attempting to reconnect (attempt %d/%d)...", retry_count, MAX_RETRIES))
    
    -- Try to reload the stream
    mp.commandv("loadfile", path, "replace")
    
    -- Reset the reconnection state after a delay
    mp.add_timeout(RETRY_DELAY, function()
        is_reconnecting = false
    end)
end

-- Check if the stream is making progress
local function check_stream()
    if is_reconnecting then return end
    
    local time_pos = mp.get_property_number("time-pos")
    local current_time = mp.get_time()
    
    if time_pos == nil then
        -- Stream not properly loaded yet
        if last_pos_time > 0 and (current_time - last_pos_time) > MAX_STALL_TIME then
            log("warn", "Stream not properly loaded, attempting to reconnect...")
            reconnect()
        end
        return
    end
    
    if time_pos == prev_pos then
        -- No progress since last check
        if last_pos_time == 0 then
            last_pos_time = current_time
        elseif (current_time - last_pos_time) > MAX_STALL_TIME then
            log("warn", "Stream stalled, attempting to reconnect...")
            reconnect()
            last_pos_time = 0
        end
    else
        -- Stream is making progress
        prev_pos = time_pos
        last_pos_time = current_time
        retry_count = 0  -- Reset retry counter on successful progress
    end
end

-- Set up event handlers
mp.register_event("end-file", function()
    log("info", "Stream ended, attempting to reconnect...")
    reconnect()
end)

mp.register_event("file-loaded", function()
    log("info", "Stream loaded successfully")
    retry_count = 0
    is_reconnecting = false
    prev_pos = 0
    last_pos_time = 0
end)

-- Set up the periodic check
mp.add_periodic_timer(CHECK_INTERVAL, check_stream)

log("info", "Reconnect script loaded")

mp.add_periodic_timer(5, check_stream)