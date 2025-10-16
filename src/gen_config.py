    def _generate_sway_config(self) -> None:
        """Generate Sway configuration using the template and append window rules.
        
        Copies the template file and appends window positioning rules for each stream.
        """
        if not self.streams:
            return
            
        config_dir = os.path.dirname(self.sway_config_path)
        os.makedirs(config_dir, exist_ok=True)
        
        # Generate window positioning rules for each stream
        window_rules = [
            f'''for_window [title="{stream_id}"] {{\n    move position {pos.x} {pos.y}\n}}'''
            for stream_id, stream in self.streams.items()
            for pos in [stream.geometry]
        ]
        
        # Append window rules to the config
        config_content += '\n'.join([''] + window_rules)
        
        # Write the final config
        with open(self.sway_config_path, 'w') as f:
            f.write(config_content)