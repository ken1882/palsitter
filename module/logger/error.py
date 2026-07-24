import os
import re
import time
from datetime import datetime

from module.logger.logger import logger


def save_error_log():
    """
    Save logs to ./logs/error/<timestamp>/log.txt
    """
    folder = f'./logs/error/{int(time.time() * 1000)}'
    logger.warning(f'Saving error: {folder}')
    os.makedirs(folder, exist_ok=True)
    with open(logger.log_file, 'r', encoding='utf-8') as f:
        lines = f.readlines()
        start = 0
        for index, line in enumerate(lines):
            line = line.strip(' \r\t\n')
            if re.match('^═{15,}$', line):
                start = index
        lines = lines[start - 2:]
    with open(f'{folder}/log.txt', 'w', encoding='utf-8') as f:
        f.writelines(lines)
