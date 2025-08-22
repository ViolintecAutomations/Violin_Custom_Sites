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

if __name__ == '__main__':
    print("Starting Waitress server...")
    try:
        serve(app, host='0.0.0.0', port=5000)
    except socket.error as e:
        print(f"Error starting server: {e}")
        print("Port 5000 might be in use. Trying another port...")
        serve(app, host='0.0.0.0', port=5001)