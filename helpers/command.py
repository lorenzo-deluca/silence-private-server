import time
import uuid
import json

class Command:
    
    def __init__(self, code, command_configuration):
        
        if self.validate_command_configuration(command_configuration) == False:
            raise ValueError("Invalid command configuration provided")
        
        self.Code = code
        self.ID = str(uuid.uuid4())
        self.command = command_configuration['command']
        self.maxretry = command_configuration['maxretry']
        self.timeout = command_configuration['timeout']

        self.TSInserted = time.time()
        self.TSExecuted = None
        self.retry_attempts = 0

    def validate_command_configuration(self, config_array):
        if 'command' in config_array and 'maxretry' in config_array and 'timeout' in config_array:
            if len(config_array['command']) > 0 and config_array['timeout'] > 0 and config_array['maxretry'] > 0:
                return True
        return False
    
    def get_command(self):
        return bytearray(self.command, 'utf-8')
    
    def retry(self):
        self.retry_attempts += 1
        if self.retry_attempts < self.maxretry:
            return True
        return False
    
    def checkTimeout(self):
        if time.time() - self.TSInserted > self.timeout:
            return True
        return False

    def execute(self):
        if time.time() - self.TSInserted > self.timeout:
            raise Exception("Command timeout")
        self.TSExecuted = time.time()
        pass

    def to_json(self):
        return json.dumps({
            "ID": self.ID,
            "Code": self.Code,
            "Command": self.command,
            "TSInserted": self.TSInserted,
            "maxretry": self.maxretry,
            "retry_attempts": self.retry_attempts
        })