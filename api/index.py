import os
os.environ.setdefault('CIME_DATA_DIR','/tmp/cime-dos-mundos-data')
os.environ.setdefault('CIME_PORT','80')
os.environ.setdefault('CIME_BIND','0.0.0.0')
os.environ.setdefault('CIME_DEPLOYMENT','vercel')
from server_v5 import Gateway, bootstrap
bootstrap()
handler = Gateway
