import os
import platform
from app import create_app
from waitress import serve
import socket

# Windows compatibility for site-packages
if platform.system() == "Windows":
    site_packages_path = os.path.join(
        os.environ['LOCALAPPDATA'],
        'Packages',
        'PythonSoftwareFoundation.Python.3.13_qbz5n2kfra8p0',
        'LocalCache', 'local-packages', 'Python313', 'site-packages'
    )
    if site_packages_path not in os.sys.path:
        os.sys.path.append(site_packages_path)

app = create_app()