from flask import Blueprint, render_template, session, current_app

# Define the CMS blueprint
cms_blueprint = Blueprint('cms', __name__)

@cms_blueprint.route('/', defaults={'path': ''})
@cms_blueprint.route('/<path:path>')
def cms_home(path):
    # If the request is for the root path (e.g., /cms or /cms/), render the index.html

@cms_blueprint.route('/status')
def cms_status():
    return "CMS Status Page"
