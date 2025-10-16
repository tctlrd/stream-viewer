#!/usr/bin/env python3
"""
Stream Manager - Handles MPV stream management independently of the Sway configuration.
"""
import os
import time
import json
import signal
import logging
import subprocess
from dataclasses import dataclass
from typing import Dict, Optional, Any, List

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger('stream_manager')

@dataclass
class StreamGeometry:
    """Represents the geometry of a stream window."""
    width: int
    height: int
    x: int
    y: int

@dataclass
class StreamConfig:
    """Configuration for a single stream."""
    id: str
    url: str
    geometry: StreamGeometry
    options: Optional[Dict[str, Any]] = None

class StreamManager:
    """Manages MPV streams independently of the Sway configuration."""
    
    def __init__(self):
        """Initialize the StreamManager."""
        self.streams: Dict[str, StreamConfig] = {}
        self.mpv_instances: Dict[str, subprocess.Popen] = {}
        self.running = False
        
        # Set up signal handlers for clean shutdown
        signal.signal(signal.SIGINT, self._handle_signal)
        signal.signal(signal.SIGTERM, self._handle_signal)
    
    def load_config(self, config_path: str) -> bool:
        """Load stream configurations from a JSON file.
        
        Args:
            config_path: Path to the JSON configuration file
            
        Returns:
            bool: True if configuration was loaded successfully
        """
        try:
            with open(config_path, 'r') as f:
                config_data = json.load(f)
            
            self.streams = {}
            streams_data = config_data.get('streams', [])
            
            # Handle both list and dict formats
            if isinstance(streams_data, list):
                for stream_data in streams_data:
                    try:
                        stream_id = stream_data['id']
                        geo = stream_data.get('geometry', {})
                        self.streams[stream_id] = StreamConfig(
                            id=stream_id,
                            url=stream_data['url'],
                            geometry=StreamGeometry(
                                width=geo.get('width', 640),
                                height=geo.get('height', 360),
                                x=geo.get('x', 0),
                                y=geo.get('y', 0)
                            ),
                            options=stream_data.get('options', {})
                        )
                    except KeyError as e:
                        logger.error(f"Missing required field in stream configuration: {e}")
                        continue
            elif isinstance(streams_data, dict):
                for stream_id, stream_data in streams_data.items():
                    try:
                        geo = stream_data.get('geometry', {})
                        self.streams[stream_id] = StreamConfig(
                            id=stream_id,
                            url=stream_data['url'],
                            geometry=StreamGeometry(
                                width=geo.get('width', 640),
                                height=geo.get('height', 360),
                                x=geo.get('x', 0),
                                y=geo.get('y', 0)
                            ),
                            options=stream_data.get('options', {})
                        )
                    except KeyError as e:
                        logger.error(f"Missing required field in stream configuration: {e}")
                        continue
            
            logger.info(f"Loaded {len(self.streams)} stream configurations")
            return True
            
        except Exception as e:
            logger.error(f"Failed to load configuration: {e}")
            return False
    
    def start_stream(self, stream: StreamConfig) -> bool:
        """Start a single MPV stream.
        
        Args:
            stream: Stream configuration
            
        Returns:
            bool: True if the stream was started successfully
        """
        if stream.id in self.mpv_instances:
            process = self.mpv_instances[stream.id]
            if process.poll() is None:  # Process is still running
                logger.warning(f"Stream {stream.id} is already running")
                return False
            else:  # Process exists but is dead
                logger.warning(f"Removing dead process for {stream.id}")
                del self.mpv_instances[stream.id]
        
        try:
            # Create log directory if it doesn't exist
            log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'logs')
            os.makedirs(log_dir, exist_ok=True)
            log_file = os.path.join(log_dir, f'mpv_{stream.id}.log')
            
            # Build MPV command
            cmd = [
                'mpv',
                '--no-config',
                '--no-audio',
                '--no-osc',
                '--no-osd-bar',
                '--no-input-default-bindings',
                '--input-vo-keyboard=no',
                f'--title={stream.id}',
                '--msg-level=all=info',
                f'--log-file={log_file}',
                '--force-window=immediate',
                f'--geometry={stream.geometry.width}x{stream.geometry.height}+{stream.geometry.x}+{stream.geometry.y}',
                stream.url
            ]
            
            # Add any custom options
            if stream.options:
                for key, value in stream.options.items():
                    if value is True:
                        cmd.append(f'--{key}')
                    elif value is not None:
                        cmd.extend([f'--{key}', str(value)])
            
            logger.info(f"Starting MPV for {stream.id} with command: {' '.join(cmd)}")
            
            # Start MPV process
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                start_new_session=True
            )
            
            self.mpv_instances[stream.id] = process
            logger.info(f"Started stream {stream.id} (PID: {process.pid})")
            return True
            
        except Exception as e:
            logger.error(f"Failed to start stream {stream.id}: {e}")
            return False
    
    def stop_stream(self, stream_id: str) -> bool:
        """Stop a running MPV stream.
        
        Args:
            stream_id: ID of the stream to stop
            
        Returns:
            bool: True if the stream was stopped successfully
        """
        if stream_id not in self.mpv_instances:
            logger.warning(f"Stream {stream_id} is not running")
            return False
        
        try:
            process = self.mpv_instances[stream_id]
            
            # Try to terminate gracefully first
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                # Force kill if it doesn't terminate
                process.kill()
                process.wait()
            
            del self.mpv_instances[stream_id]
            logger.info(f"Stopped stream {stream_id}")
            return True
            
        except Exception as e:
            logger.error(f"Error stopping stream {stream_id}: {e}")
            return False
    
    def start_all(self) -> None:
        """Start all configured streams."""
        if not self.streams:
            logger.warning("No streams configured")
            return
        
        self.running = True
        
        # Start each stream with a small delay between them
        for i, (stream_id, stream) in enumerate(self.streams.items()):
            if self.start_stream(stream):
                time.sleep(0.5)  # Small delay to stagger stream starts
    
    def stop_all(self) -> None:
        """Stop all running streams."""
        for stream_id in list(self.mpv_instances.keys()):
            self.stop_stream(stream_id)
    
    def _handle_signal(self, signum, frame):
        """Handle termination signals."""
        logger.info(f"Received signal {signum}, shutting down...")
        self.running = False
        self.stop_all()
    
    def run(self, config_path: str) -> None:
        """Run the stream manager with the given configuration.
        
        Args:
            config_path: Path to the configuration file
        """
        if not self.load_config(config_path):
            return
        
        try:
            self.start_all()
            
            # Main loop
            while self.running:
                time.sleep(1)
                
                # Check for dead processes
                dead_streams = []
                for stream_id, process in self.mpv_instances.items():
                    if process.poll() is not None:  # Process has terminated
                        logger.warning(f"Stream {stream_id} died with code {process.returncode}")
                        dead_streams.append(stream_id)
                
                # Restart dead streams
                for stream_id in dead_streams:
                    if stream_id in self.mpv_instances:
                        del self.mpv_instances[stream_id]
                    if stream_id in self.streams and self.running:
                        logger.info(f"Restarting stream {stream_id}")
                        self.start_stream(self.streams[stream_id])
                        
        except KeyboardInterrupt:
            logger.info("Shutting down...")
        except Exception as e:
            logger.error(f"Error in main loop: {e}")
        finally:
            self.stop_all()

def main():
    """Main entry point for the stream manager."""
    import argparse
    
    parser = argparse.ArgumentParser(description='MPV Stream Manager')
    parser.add_argument('-c', '--config', required=True, help='Path to configuration file')
    args = parser.parse_args()
    
    manager = StreamManager()
    manager.run(args.config)

if __name__ == '__main__':
    main()
