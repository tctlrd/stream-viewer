local prev_pos = 0

function check_stream()
  local time_pos = mp.get_property_number('time-pos')
  if time_pos == prev_pos then
    mp.commandv('loadfile', mp.get_property('path'), 'replace')
  end
  prev_pos = time_pos
end

mp.add_periodic_timer(5, check_stream)