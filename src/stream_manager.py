"""Stream Viewer - An async multi-stream viewer using MPV for playback."""
import asyncio
import json
import logging
import os
import signal
import time
from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple, Tuple

# Configure logging
def setup_logging():
    """Set up logging configuration."""
    log_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'logs')
    os.makedirs(log_dir, exist_ok=True)
    
    log_file = os.path.join(log_dir, 'stream_viewer.log')
    
    # Clear previous log file
    with open(log_file, 'w'):
        pass
    
    # Configure root logger
    logger = logging.getLogger()
    logger.setLevel(logging.DEBUG)
    
    # Console handler (INFO level)
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    console_handler.setFormatter(console_formatter)
    
    # File handler (DEBUG level)
    file_handler = logging.FileHandler(log_file)
    file_handler.setLevel(logging.DEBUG)
    file_formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    file_handler.setFormatter(file_formatter)
    
    # Add handlers
    logger.addHandler(console_handler)
    logger.addHandler(file_handler)
    
    return logging.getLogger('stream_viewer')

# Initialize logging
logger = setup_logging()

@dataclass
class GeometryConfig:
    x: int
    y: int
    width: int
    height: int

@dataclass
class StreamConfig:
    id: str
    url: str
    geometry: GeometryConfig = field(default_factory=lambda: GeometryConfig(0, 0, 640, 360))

class StreamViewer:
    """Manages multiple MPV streams asynchronously."""
    
    def __init__(self, config_path: str = None):
        self.streams: Dict[str, StreamConfig] = {}
        self.mpv_instances: Dict[str, asyncio.subprocess.Process] = {}
        self.running = False
        self.config_path = config_path
        self._monitor_task: Optional[asyncio.Task] = None
        self._stop_event = asyncio.Event()
        
        # Setup signal handlers
        signal.signal(signal.SIGINT, self._handle_signal)
        signal.signal(signal.SIGTERM, self._handle_signal)

    def _handle_signal(self, signum, frame) -> None:
        """Handle termination signals."""
        logger.info(f"Received signal {signum}, shutting down...")
        self._stop_event.set()
        if self._monitor_task:
            self._monitor_task.cancel()

    async def start_stream(self, stream: StreamConfig) -> bool:
        """Start a stream asynchronously."""
        if stream.id in self.mpv_instances:
            process = self.mpv_instances[stream.id]
            if process.returncode is None:
                logger.warning(f"Stream {stream.id} is already running")
                return False
            else:
                logger.warning(f"Removing dead process for {stream.id}")
                del self.mpv_instances[stream.id]
        
        try:
            # Create log directory if it doesn't exist
            log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'logs')
            os.makedirs(log_dir, exist_ok=True)
            log_file = os.path.join(log_dir, f'mpv_{stream.id}.log')
            
            # Create a unique IPC socket for each stream
            ipc_socket = f'/tmp/mpv-ipc-{stream.id}'
            
            # Build MPV command with IPC enabled
            cmd = [
                'mpv',
                '--no-config',
                '--no-audio',
                '--no-osc',
                '--no-osd-bar',
                '--no-input-default-bindings',
                '--profile=low-latency',
                '--hwdec=vaapi',
                f'--input-ipc-server={ipc_socket}',  # Unique IPC socket for each stream
                '--input-vo-keyboard=no',
                f'--title={stream.id}',
                '--msg-level=ffmpeg/demuxer=error',
                f'--log-file={log_file}',
                '--window-scale=1.0',
                '--window-minimized=no',
                '--no-window-dragging',
                '--idle=yes',  # Keep MPV running even if stream fails
                '--force-window=immediate',
                '--no-terminal',  # Don't use terminal for input
                '--no-keepaspect-window',
                f'--geometry={stream.geometry.width}x{stream.geometry.height}+{stream.geometry.x}+{stream.geometry.y}',
                stream.url
            ]
            logger.info(f"Starting MPV with command: {' '.join(cmd)}")
            
            # Start MPV process asynchronously
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                close_fds=True
            )
            
            # Wait for process to start
            try:
                await asyncio.wait_for(process.wait(), timeout=2.0)
                # If we get here, the process exited quickly
                stderr = await process.stderr.read()
                logger.error(f"MPV failed to start for {stream.id}. Error: {stderr.decode()}")
                return False
            except asyncio.TimeoutError:
                pass
            
            self.mpv_instances[stream.id] = process
            logger.info(f"Started stream {stream.id} (PID: {process.pid})")
            
            # Start monitoring the stream
            asyncio.create_task(self._monitor_stream(stream.id, process))
            return True
            
        except Exception as e:
            logger.error(f"Failed to start stream {stream.id}", exc_info=True)
            return False

    async def stop_stream(self, stream_id: str) -> bool:
        """Stop a running MPV instance asynchronously."""
        if stream_id not in self.mpv_instances:
            logger.warning(f"Stream {stream_id} is not running")
            return False
        
        try:
            process = self.mpv_instances[stream_id]
            await self._terminate_process(process)
            del self.mpv_instances[stream_id]
            logger.info(f"Stopped stream {stream_id}")
            return True
            
        except Exception as e:
            logger.error(f"Error stopping stream {stream_id}: {e}")
            return False

    async def start_all(self) -> None:
        """Start all configured streams asynchronously."""
        if not self.streams:
            logger.warning("No streams configured")
            return
        
        self.running = True
        self._stop_event.clear()
        
        # Start each stream with a small delay between them
        for stream_id, stream in self.streams.items():
            if self._stop_event.is_set():
                break
            await self.start_stream(stream)
            await asyncio.sleep(0.3)  # Small delay to stagger stream starts
    
    async def stop_all(self) -> None:
        """Stop all running streams asynchronously."""
        self.running = False
        self._stop_event.set()
        
        # Stop all MPV instances in parallel
        tasks = [self.stop_stream(stream_id) 
                for stream_id in list(self.mpv_instances.keys())]
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _terminate_process(self, process: asyncio.subprocess.Process, timeout: float = 2.0) -> None:
        """Safely terminate a process with a timeout."""
        if process.returncode is not None:
            return
            
        process.terminate()
        try:
            await asyncio.wait_for(process.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            process.kill()
            try:
                await process.wait()
            except:
                pass

    def _get_frame_info(self, pid: int) -> Tuple[float, int]:
        """Get frame count and timestamp from /proc/pid/status.
        
        Returns:
            Tuple[float, int]: (timestamp, rss_kb)
        """
        try:
            with open(f'/proc/{pid}/status') as f:
                for line in f:
                    if line.startswith('RssAnon:'):
                        rss = int(line.split()[1])  # in kB
                        return time.monotonic(), rss
            return time.monotonic(), 0
        except (FileNotFoundError, ProcessLookupError):
            # Process doesn't exist anymore
            return time.monotonic(), 0
        except Exception as e:
            logger.warning(f"Error getting frame info for PID {pid}: {e}")
            return time.monotonic(), 0

    async def _is_stream_alive(self, stream_id: str, process: asyncio.subprocess.Process) -> Tuple[bool, str]:
        """Check if the stream is still active and updating.
        
        Returns:
            Tuple[bool, str]: (is_alive, status_message)
        """
        try:
            # Check if process is still running
            if process.returncode is not None:
                return False, f"process exited with code {process.returncode}"
                
            # Check if process is still responding
            try:
                process.send_signal(0)  # Check if process is alive
            except ProcessLookupError:
                return False, "process not found"
                
            # Initialize frame info tracking if needed
            if not hasattr(self, '_last_frame_info'):
                self._last_frame_info = {}
            
            # Get current memory info
            current_time, current_rss = self._get_frame_info(process.pid)
            last_time, last_rss = self._last_frame_info.get(stream_id, (0, 0))
            self._last_frame_info[stream_id] = (current_time, current_rss)
            
            # If we don't have previous data yet, assume it's alive
            if last_time == 0 or last_rss == 0:
                return True, "initializing"
                
            # Calculate time and memory differences
            time_diff = current_time - last_time
            rss_diff = abs(current_rss - last_rss)
            
            # If memory hasn't changed in 10 seconds, consider it frozen
            if time_diff > 10.0 and rss_diff < 10:  # Less than 10KB change in 10s
                return False, f"stream frozen (RSS changed by {rss_diff}KB in {time_diff:.1f}s)"
                
            return True, f"active (RSS: {current_rss}KB, Δ: {rss_diff}KB)"
            
        except (BrokenPipeError, ConnectionResetError, ProcessLookupError):
            return False
        except Exception as e:
            logger.warning(f"Error checking stream status: {e}")
            return False

    async def _monitor_stream(self, stream_id: str, process: asyncio.subprocess.Process) -> None:
        """Monitor a single stream for health and stability."""
        last_activity = time.monotonic()
        CHECK_INTERVAL = 2.0
        MAX_INACTIVITY = 10.0  # seconds
        last_status = ""
        
        try:
            # Initial delay to let the stream stabilize
            await asyncio.sleep(5.0)
            
            while not self._stop_event.is_set() and process.returncode is None:
                try:
                    # Check if stream is still alive and updating
                    is_alive, status = await self._is_stream_alive(stream_id, process)
                    current_time = time.monotonic()
                    
                    # Log status changes
                    if status != last_status:
                        logger.info(f"Stream {stream_id} status: {status}")
                        last_status = status
                    
                    if is_alive:
                        last_activity = current_time
                        await asyncio.sleep(CHECK_INTERVAL)
                    elif (current_time - last_activity) > MAX_INACTIVITY:
                        logger.warning(f"Stream {stream_id} inactive for {MAX_INACTIVITY}s, restarting...")
                        await self._terminate_process(process)
                        break
                    
                    # Read and log process output
                    try:
                        line = await asyncio.wait_for(process.stderr.readline(), timeout=1.0)
                        if line:
                            logger.debug(f"MPV {stream_id}: {line.decode().strip()}")
                    except asyncio.TimeoutError:
                        pass
                    except Exception as e:
                        logger.warning(f"Error reading from MPV {stream_id}: {e}")
                    
                    # Wait for next check
                    await asyncio.sleep(CHECK_INTERVAL)
                    
                except Exception as e:
                    logger.error(f"Error monitoring stream {stream_id}: {e}")
                    break
                    
        except asyncio.CancelledError:
            logger.debug(f"Monitoring for {stream_id} cancelled")
        except Exception as e:
            logger.error(f"Unexpected error in monitor for {stream_id}: {e}")
        finally:
            # Clean up if process is still running
            if process.returncode is None:
                logger.info(f"Terminating stream {stream_id}")
                await self._terminate_process(process)

    async def monitor(self) -> None:
        """Monitor and automatically restart failed streams with backoff."""
        import time
        
        # Track retry attempts and timestamps
        retry_attempts = {}
        MAX_RETRIES = 5
        INITIAL_RETRY_DELAY = 2.0  # Start with 2 seconds
        MAX_RETRY_DELAY = 60.0     # Cap at 1 minute between retries
        
        while not self._stop_event.is_set():
            try:
                # Check all streams
                for stream_id, process in list(self.mpv_instances.items()):
                    if self._stop_event.is_set():
                        return
                        
                    # Skip if process is still running
                    if process.returncode is None:
                        if stream_id in retry_attempts:
                            # Reset retry counter for successful streams
                            logger.info(f"Stream {stream_id} recovered, resetting retry counter")
                            del retry_attempts[stream_id]
                        continue
                        
                    # Process has terminated
                    if stream_id not in retry_attempts:
                        retry_attempts[stream_id] = {
                            'attempts': 0,
                            'next_retry': time.monotonic(),
                            'delay': INITIAL_RETRY_DELAY
                        }
                    
                    # Check if it's time to retry
                    current_time = time.monotonic()
                    retry_info = retry_attempts[stream_id]
                    
                    if current_time >= retry_info['next_retry']:
                        retry_info['attempts'] += 1
                        
                        if retry_info['attempts'] > MAX_RETRIES:
                            logger.error(f"Max retries reached for {stream_id}. Giving up.")
                            del self.mpv_instances[stream_id]
                            continue
                        
                        # Calculate next retry with exponential backoff
                        delay = min(retry_info['delay'] * 2, MAX_RETRY_DELAY)
                        retry_info['delay'] = delay
                        retry_info['next_retry'] = current_time + delay
                        
                        # Try to restart
                        if stream_id in self.streams:
                            logger.info(f"Attempting to restart {stream_id} (attempt {retry_info['attempts']}/{MAX_RETRIES}, next retry in {delay:.1f}s)")
                            try:
                                await self.start_stream(self.streams[stream_id])
                                # Reset retry info on successful start
                                if stream_id in self.mpv_instances:
                                    del retry_attempts[stream_id]
                            except Exception as e:
                                logger.error(f"Failed to restart {stream_id}: {e}")
                
                # Wait before next check
                await asyncio.sleep(2.0)
                
            except asyncio.CancelledError:
                logger.debug("Monitoring task cancelled")
                return
            except Exception as e:
                logger.error(f"Error in monitor loop: {e}")
                await asyncio.sleep(5.0)  # Prevent tight error loops

    def load_config(self, config_path: str) -> bool:
        """Load configuration from file."""
        try:
            with open(config_path, 'r') as f:
                config = json.load(f)
            
            # Load stream configs
            if 'streams' in config and isinstance(config['streams'], list):
                for stream_cfg in config['streams']:
                    geometry_cfg = stream_cfg.pop('geometry', {})
                    stream = StreamConfig(
                        id=stream_cfg['id'],
                        url=stream_cfg['url'],
                        geometry=GeometryConfig(
                            x=geometry_cfg.get('x', 0),
                            y=geometry_cfg.get('y', 0),
                            width=geometry_cfg.get('width', 640),
                            height=geometry_cfg.get('height', 360)
                        )
                    )
                    self.streams[stream.id] = stream

                logger.info(f"Loaded configuration for {len(self.streams)} streams")
                return True
            return False
            
        except Exception as e:
            logger.error(f"Error loading config: {e}")
            return False

    async def run(self) -> None:
        """Run the stream viewer asynchronously."""
        try:
            if self.config_path and os.path.exists(self.config_path):
                self.load_config(self.config_path)
            
            logger.info("Starting Stream Viewer")
            await self.start_all()
            
            # Enable monitoring in the background
            self._monitor_task = asyncio.create_task(self.monitor())
            
            # Wait for stop event
            await self._stop_event.wait()
            
        except asyncio.CancelledError:
            logger.info("Shutdown requested")
        except Exception as e:
            logger.error(f"Unexpected error: {e}", exc_info=True)
        finally:
            logger.info("Shutting down streams...")
            await self.stop_all()
            logger.info("Stream Viewer stopped")

async def async_main():
    """Async main entry point."""
    import argparse
    
    parser = argparse.ArgumentParser(description='Stream Viewer - An async multi-stream viewer using MPV')
    parser.add_argument('-c', '--config', default='config/streams.json',
                      help='Path to configuration file')
    parser.add_argument('--debug', action='store_true',
                      help='Enable debug logging')
    args = parser.parse_args()
    
    # Enable debug logging if requested
    if args.debug:
        for handler in logging.getLogger().handlers:
            if isinstance(handler, logging.StreamHandler):
                handler.setLevel(logging.DEBUG)
    
    # Run the viewer
    try:
        viewer = StreamViewer(args.config)
        await viewer.run()
        return 0
    except Exception as e:
        logger.critical(f"Fatal error: {e}", exc_info=True)
        return 1
    except asyncio.CancelledError:
        logger.info("Shutting down...")
        return 0

def main():
    """Synchronous main entry point that runs the async main."""
    return asyncio.run(async_main())

if __name__ == '__main__':
    main()