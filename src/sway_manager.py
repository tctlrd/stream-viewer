#!/usr/bin/env python3
import os
import sys
import json
import logging
import subprocess
import argparse
from pathlib import Path

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger('sway_manager')

def generate_sway_config(config_path: str) -> bool:
    """Generate Sway configuration from template and streams."""
    config_dir = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 
        'config'
    )
    template_path = os.path.join(config_dir, 'sway_config_template.in')
    sway_config_path = os.path.join(config_dir, 'sway_config')
    
    try:
        # Ensure config directory exists
        os.makedirs(config_dir, exist_ok=True)
        
        # Load template
        with open(template_path, 'r', encoding='utf-8') as f:
            template = f.read()
        
        # Load streams configuration
        with open(config_path, 'r') as f:
            config = json.load(f)
        
        # Generate window positioning commands for each stream
        window_rules = []
        for stream in config.get('streams', []):
            geo = stream.get('geometry', {})
            x = geo.get('x', 0)
            y = geo.get('y', 0)
            
            # Create window rule for this stream
            rule = f'''for_window [title="{stream['id']}"] {{
    move position {x} {y}
}}'''
            window_rules.append(rule)
        
        # Combine template with generated window rules
        config_content = template
        if window_rules:
            config_content = template + "\n\n" + "\n\n".join(window_rules)
        
        # Write config file
        with open(sway_config_path, 'w', encoding='utf-8') as f:
            f.write(config_content)
        
        # Set appropriate permissions
        os.chmod(sway_config_path, 0o644)
        
        logger.info(f"Successfully generated Sway config at {sway_config_path}")
        return True
        
    except Exception as e:
        logger.error(f"Error generating Sway config: {e}", exc_info=True)
        return False

def start_sway(config_path: str) -> None:
    """Start Sway with the generated configuration."""
    try:
        # Generate config first
        if not generate_sway_config(config_path):
            logger.error("Failed to generate Sway configuration")
            sys.exit(1)
        
        # Get config directory
        config_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 
            'config'
        )
        sway_config_path = os.path.join(config_dir, 'sway_config')
        
        # Start Sway and let it take over
        logger.info("Starting Sway...")
        os.execvp('sway', ['sway', '--config', sway_config_path])
        
    except Exception as e:
        logger.error(f"Failed to start Sway: {e}", exc_info=True)
        sys.exit(1)

def main() -> None:
    parser = argparse.ArgumentParser(description='Sway Launcher')
    parser.add_argument(
        '-c', '--config',
        required=True,
        help='Path to the configuration file',
        metavar='PATH'
    )
    parser.add_argument(
        '-v', '--verbose',
        action='store_true',
        help='Enable verbose logging'
    )
    
    args = parser.parse_args()
    
    # Configure logging level
    if args.verbose:
        logger.setLevel(logging.DEBUG)
        logger.debug("Verbose logging enabled")
    
    # Verify config file exists and is readable
    config_path = os.path.abspath(args.config)
    if not os.path.isfile(config_path):
        logger.error(f"Configuration file not found: {config_path}")
        sys.exit(1)
    
    if not os.access(config_path, os.R_OK):
        logger.error(f"Cannot read configuration file: {config_path}")
        sys.exit(1)
    
    # Start Sway (this function won't return if successful)
    start_sway(config_path)

if __name__ == '__main__':
    main()
