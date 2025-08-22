import os
import platform
import sys # Import sys
import csv # Import csv for reading the config file
import importlib.util # For dynamic module loading
from werkzeug.middleware.dispatcher import DispatcherMiddleware
from werkzeug.serving import run_simple
from flask import Flask, render_template

from CSV_Param import CSV_Proj_Params # Import the new config loader

# Load the project names from the Project Code Init PY
from CMS_Pro.app import Curr_Proj_Name as CMS_AppName, create_app as create_cms_app_factory
from PR_CREATOR.PO_App import Curr_Proj_Name as PR_Creator_AppName, app as pr_creator_app

# Create the CMS app instance
cms_app_instance = create_cms_app_factory()
print(f"DEBUG: cms_app_instance type: {type(cms_app_instance)}")
print(f"DEBUG: cms_app_instance: {cms_app_instance}")

# List of project names to be used in the dispatcher
projects = [CMS_AppName, PR_Creator_AppName]

# Create a simple Flask app for the loading page
loading_app = Flask(__name__)

#XXXXXXXXXXXXXXXXXX
#console.log(CMS_AppName)
#  + CSV_Proj_Params(PR_Creator_AppName).get('Web_Suffix')

application = DispatcherMiddleware(loading_app, {
    '/cms': cms_app_instance, # Pass the *instance* of the Flask app
    '/sap': pr_creator_app
})

# Windows compatibility for site-packages (from CMS_Pro - Copy/run.py)
if platform.system() == "Windows":
    # Explicitly add the user site-packages path where flask_babel is installed
    user_site_packages = r'c:\users\vtgs_lap_01\appdata\local\packages\pythonsoftwarefoundation.python.3.13_qbz5n2kfra8p0\localcache\local-packages\python313\site-packages'
    if user_site_packages not in os.sys.path:
        os.sys.path.append(user_site_packages)

Web_Projects = []
for project in projects:
    proj = {
        "Button_Text": CSV_Proj_Params(project).get('Button_Text'),
        "Web_Suffix": CSV_Proj_Params(project).get('Web_Suffix')
    }
    Web_Projects.append(proj)


@loading_app.route('/')
def loading_page():
    return render_template('loading.html', projects=Web_Projects)

if __name__ == '__main__':
    port = 5000
    host = '0.0.0.0'
    print(f"🚀 Starting combined server on http://localhost:{port}")
    for project in projects:
        print(f"🌍 {project} accessible at http://localhost:{port}/{CSV_Proj_Params(project).get('Web_Suffix')}")
    run_simple(host, port, application, use_reloader=True, use_debugger=True)
