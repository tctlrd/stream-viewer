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
        
        # Setup signal handlers
        signal.signal(signal.SIGINT, self._handle_signal)
        signal.signal(signal.SIGTERM, self._handle_signal)

    def _handle_signal(self, signum, frame) -> None:
        """Handle termination signals."""
        logger.info(f"Received signal {signum}, shutting down...")
        self.running = False

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
            
            # Build MPV command with IPC enabled
            cmd = [
                'mpv',
                f'--scripts-append={os.path.join(os.path.dirname(os.path.abspath(__file__)), "reconnect.lua")}',
                '--keep-open=yes',
                '--keep-open-pause=no',
                '--auto-window-resize=no',
                '--no-config',
                '--no-audio',
                '--no-osc',
                '--no-osd-bar',
                '--no-input-default-bindings',
                '--profile=low-latency',
                '--hwdec=vaapi',
                '--input-vo-keyboard=no',
                f'--title={stream.id}',
                '--really-quiet',
                '--msg-level=all=warn,ffmpeg=no,vo/gpu=error,vo/gpu/libplacebo=error',
                f'--log-file={log_file}',
                '--window-scale=1.0',
                '--window-minimized=no',
                '--no-window-dragging',
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
            return False

    async def start_all(self) -> None:
        """Start all configured streams asynchronously."""
        if not self.streams:
            logger.warning("No streams configured to start")
            return
        
        self.running = True
        
        # Start each stream with a small delay between them
        for stream_id, stream in self.streams.items():
            if not self.running:
                break
            await self.start_stream(stream)
            await asyncio.sleep(0.3)  # Small delay to stagger stream starts
    
    async def stop_all(self) -> None:
        """Stop all running streams asynchronously."""
        self.running = False
        
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
            
            # Keep the application running until stopped
            self.running = True
            while self.running:
                await asyncio.sleep(1)
            
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