# Config stuff
import getpass # for the TS and Slack tokens if they're not provided
import configparser # to parse config.ini

# Communications
import tableauserverclient as TSC # for setting up the webhook on Tableau Server and getting metadata
from http.server import HTTPServer, BaseHTTPRequestHandler # for listening to the webhook
import ssl # for "encapsulating" HTTPServer
from io import BytesIO # for the responses we send
import slack # for obvious reasons

# Parsing, files, OS
import re # for matching the listener URL path
import json # for parsing
import os # because we write the thumbnail of e.g. a new workbook to a temp file, and so we can remove it
import signal, sys # for catching the exit

#### Parse config file
config = configparser.ConfigParser()
config.read("./config.ini")

# Prompt for credentials that weren't included.
if "pat_name" in config["Tableau Server"]:
    pat_name = config["Tableau Server"]["pat_name"]
elif os.getenv("TS_PAT_NAME") is not None:
    pat_name = os.getenv("TS_PAT_NAME")
else:
    pat_name = getpass.getpass("Tableau Server Personal Access Token Name: ")

if "pat_secret" in config["Tableau Server"]:
    pat_secret = config["Tableau Server"]["pat_secret"]
elif os.getenv("TS_PAT_SECRET") is not None:
    pat_secret = os.getenv("TS_PAT_SECRET")
else:
    pat_secret = getpass.getpass("Tableau Server Personal Access Token Secret for Token " + pat_name + ": ")

if "slack_token" in config["Slack"]:
    slack_token = config["Slack"]["slack_token"]
elif os.getenv("SLACK_TOKEN") is not None:
    slack_token = os.getenv("SLACK_TOKEN")
else:
    slack_token = getpass.getpass("Slack token: ")

#### Authenticate to Tableau Server
# After we have stored the site name as "Default" in the keyring, we're going to call it "" because that's how Tableau wants it.
tableau_site = "" if config["Tableau Server"]["site"] == "Default" else config["Tableau Server"]["site"]

print("")
print("Target Tableau Server > Site: " + config["Tableau Server"]["server"] + " > \"" + tableau_site + "\"")

ts_auth = TSC.PersonalAccessTokenAuth(token_name=pat_name, personal_access_token=pat_secret, site_id=tableau_site)
tableau_server = TSC.Server(config["Tableau Server"]["server"])
tableau_server.version = "3.8" # Needed somewhow because otherwise it aims for a version way too low
if "ssl_certificates" in config["Tableau Server"]:
    tableau_server.add_http_options({ "verify": config["Tableau Server"]["ssl_certificates"] })

print("Signing in to Tableau Server with token name \"" + pat_name + "\".")
try:
    tableau_server.auth.sign_in_with_personal_access_token(ts_auth)
except Exception as e:
    print("Failed to sign in to Tableau Server. Quitting.")
    print(e)
    exit(1)

#### Create webhooks
# In a future iteration, we will approach this differently:
# * We will create all webhooks by default, with a fixed set of names e.g. tswts-workbook-created, tswts-workbook-updated, etc. We can loop over a list/dict to create them.
# * We can allow for exclusions with the config file, removing them from the list if we wish.
# * We can all point them to the same URL (?) and handle the actions differently based on the body (event_type)
# * We will also clean them each time we start (not only when we stop) to avoid having duplicates

webhooks = [
    {
        "event_name": "workbook-created",
        "name": "tswts-workbook-created"
    },
    {
        "event_name": "workbook-updated", 
        "name": "tswts-workbook-updated"
    },
    {
        "event_name": "workbook-deleted", 
        "name": "tswts-workbook-deleted"
    },
    {
        "event_name": "workbook-refresh-failed", 
        "name": "tswts-workbook-refresh-failed"
    },
    {
        "event_name": "datasource-created", 
        "name": "tswts-datasource-created"
    },
    {
        "event_name": "datasource-updated", 
        "name": "tswts-datasource-updated"
    },
    {
        "event_name": "datasource-deleted", 
        "name": "tswts-datasource-deleted"
    },
    {
        "event_name": "datasource-refresh-failed", 
        "name": "tswts-datasource-refresh-failed"
    }
]

print("Spring cleaning: Deleting existing \"duplicate\" webhooks on Tableau Server, that have the same name.")
ts_existing_webhooks, pagination_itom = tableau_server.webhooks.get()
for ts_existing_webhook in ts_existing_webhooks:
    if(ts_existing_webhook.name in [webhook["name"] for webhook in webhooks]):
        print("Deleting existing webhook " + ts_existing_webhook.name + " as we're recreating it.")
        tableau_server.webhooks.delete(ts_existing_webhook.id)

print("Creating webhooks on Tableau Server")

for webhook in webhooks:

    webhook_model = TSC.WebhookItem()
    webhook_model.name = webhook["name"]
    webhook_model.event = webhook["event_name"]
    webhook_model.url = config["Webhook Configuration"]["webhook_url"]

    try:
        ts_webhook = tableau_server.webhooks.create(webhook_model)
        print("Webhook created: \"" + ts_webhook.name + "\" with id " + ts_webhook._id)
        # Store ID so we can delete them later
        webhook["id"] = ts_webhook._id
    except Exception as e:
        print("Failed to create Webhook.")
        print(e)
        exit(1)

#### Sign out of Tableau Server once the Webhook has been created, because this thing might be up for a long time and our token will have expired.
print("Signing out of Tableau Server.")
try:
    tableau_server.auth.sign_out()
except:
    print("Failed to sign out of Tableau Server. But we don't really care, do we.")

#### Start serving _our_ endpoint that the webhook will talk to

# Determine the path we've specified in the URL above, to ensure the webhook talks to the right thing (and it's not anyone else)
try:
    listener_path = re.search(r"\.[\w]+\:?[\d]{0,5}(\/[\w\d\-\_]+)", config["Webhook Configuration"]["webhook_url"]).group(1)
    print("Found listener path \"" + listener_path + "\" in URL.")
except Exception as e:
    print("Couldn't extract a path from the URL: " + config["Webhook Configuration"]["webhook_url"])
try:
    listener_port_re_search = re.search(r"\.[\w]+\:?([\d]{0,5})\/[\w\d\-\_]+", config["Webhook Configuration"]["webhook_url"])
    if listener_port_re_search is not None:
        listener_port = int(listener_port_re_search.group(1))
        print("Found listener port \"" + str(listener_port) + "\" in URL.")
    else:
        listener_port = 443
except Exception as e:
    print("Couldn't extract a port from the URL: " + config["Webhook Configuration"]["webhook_url"])

# Determine if we need CA Certs:
if "webhook_listener_ssl_ca_cert" in config["Listener"]:
    webhook_listener_ssl_ca_cert = config["Listener"]["webhook_listener_ssl_ca_cert"]
else:
    webhook_listener_ssl_ca_cert = ""

class SimpleHTTPRequestHandler(BaseHTTPRequestHandler):

    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Hello! We're here to listen to requests from Tableau Server webhooks!")
        print("The path in this request is \"" + self.path + "\".")

    def do_POST(self):
        print("Received POST request for path " + self.path)
        if self.path == listener_path:
            print("That matches our listener path!")
            content_length = int(self.headers["Content-Length"])
            body = self.rfile.read(content_length)
            self.send_response(200)
            self.end_headers()
            response = BytesIO()
            response.write(b"This is a POST request!")
            response.write(b"Received: ")
            response.write(body)
            self.wfile.write(response.getvalue())
            
            # Parse the JSON
            body_json = json.loads(json.loads(json.dumps(body.decode("utf-8"))))
            # print(body_json)
            resource_name = body_json["resource_name"]
            resource_type = body_json["resource"].title()
            resource_luid = body_json["resource_luid"]
            site_luid = body_json["site_luid"]
            event_type = body_json["event_type"]
            created_at = body_json["created_at"]
            print("Received event \"" + event_type + "\" for resource " + resource_name)

            # We'll handle the event in a generic way. The logic is:
            # If it's not been deleted, we get the URL (so we can access it)
            # If it's a workbook and it hasn't been deleted, we get the thumbnail

            try:
                
                # Can only get metadata if it wasn't deleted
                if event_type not in ["WorkbookDeleted", "DatasourceDeleted"]:
                    
                    # Sign in again, get the metadata, etc.
                    tableau_server.auth.sign_in_with_personal_access_token(ts_auth)

                    # We'll look for either a data source or workbook
                    ts_resource_endpoint = tableau_server.workbooks if resource_type == "Workbook" else tableau_server.datasources

                    ts_resource = ts_resource_endpoint.get_by_id(resource_luid)
                    ts_resource_owner = tableau_server.users.get_by_id(ts_resource.owner_id)._name
                    
                    # URL if not deleted
                    ts_resource_url = re.sub("(https?:\/\/[\d\w\-\_\.]+)\/", config["Tableau Server"]["server"] + "/", ts_resource.webpage_url)
                    
                    # Image in workbook, too
                    if resource_type == "Workbook":
                
                        tableau_server.workbooks.populate_preview_image(ts_resource)
                        workbook_image = ts_resource.preview_image

                        temp_file = "temp/" + resource_luid + ".png"
                        with open(temp_file, "wb") as image_file:
                            image_file.write(workbook_image)
                        print("Saved image to temp file: " + temp_file)

                    # Sign out of Tableau Server again, as we're done for now
                    print("Signing out of Tableau Server.")
                    try:
                        tableau_server.auth.sign_out()
                    except:
                        print("Failed to sign out of Tableau Server. But we don't really care, do we.")

                #### Post to Slack! Finally...

                try:

                    slack_web_client = slack.WebClient(token=slack_token)

                    # Message if not deleted
                    if event_type not in ["WorkbookDeleted", "DatasourceDeleted"]:
                        slack_message_text = "A " + resource_type + " was published or updated on our Tableau Server! The owner is " + ts_resource_owner + " and it's titled <" + ts_resource_url + "|*" + resource_name + "*>."
                        # Message if workbook: add message suffix and image. Also post because the method is different.
                        if resource_type == "Workbook":
                            slack_message_text = slack_message_text + " Here is what it looks like."
                            # Post!
                            try:
                                print("Posting to Slack!")
                                upload_response = slack_web_client.files_upload(file=temp_file, channels=config["Slack"]["slack_channel"], initial_comment=slack_message_text) 
                                # print(upload_response)
                            except slack.errors.SlackApiError as e:
                                print(e.response)
                            # Remove the file after we are done.
                            os.remove(temp_file)
                        else: # It's a data source: no image, just the other info posted
                            try:
                                print("Posting to Slack!")
                                post_response = slack_web_client.chat_postMessage(channel=config["Slack"]["slack_channel"], text=slack_message_text)
                                # print(post_response)
                            except slack.errors.SlackApiError as e:
                                print(e.response)
                    else:
                        slack_message_text = "A " + resource_type + " was deleted from our Tableau Server! It was titled *" + resource_name + "*. But yeah, it's gone now."
                        try:
                            print("Posting to Slack!")
                            post_response = slack_web_client.chat_postMessage(channel=config["Slack"]["slack_channel"], text=slack_message_text)
                            # print(post_response)
                        except slack.errors.SlackApiError as e:
                            print(e.response)
                    

                except Exception as e:
                    print("Failed to sign in to Slack or something.")
                    print(e)


            except Exception as e:
                print("Failed to sign in to Tableau Server and get the metadata. Not quitting, but not happy either.")
                print(e)

        else:
            self.send_response(400)
            self.end_headers()
            response = BytesIO()
            response.write(b"Incorrect listener path in URL. ")
            self.wfile.write(response.getvalue())

#### The section below is the cleanup and the exit; we define it before we launch the server

def exit_gracefully(signal, frame):
    print("Received KeyboardInterrupt or SIGINT")

    # Sign in to Tableau Server again, because our session from before is probably expired
    print("Signing in to Tableau Server with token name \"" + pat_name + "\".")
    try:
        tableau_server.auth.sign_in_with_personal_access_token(ts_auth)
    except Exception as e:
        print("Failed to sign in to Tableau Server. Quitting. The webhook will still exist there.")
        print(e)
        exit(1)

    #### Delete the webhook when we shut down (or right now for testing purposes)
    print("Deleting webhooks we created.")
    for webhook in webhooks:
        try:
            tableau_server.webhooks.delete(webhook["id"])
        except Exception as e:
            print("Failed to delete webhook. Leaving it there...")
            print(e)

    #### Sign out of Tableau Server once the Webhook has been removed
    print("Signing out of Tableau Server.")
    try:
        tableau_server.auth.sign_out()
    except:
        print("Failed to sign out of Tableau Server. But we're at the end of the script.")
    
    exit(0)
    
signal.signal(signal.SIGINT, exit_gracefully)

# This is it.
print("Starting HTTPServer for our listener application.")
httpd = HTTPServer(("", listener_port), SimpleHTTPRequestHandler)
httpd.socket = ssl.wrap_socket(httpd.socket, keyfile=config["Listener"]["webhook_listener_ssl_key"], certfile=config["Listener"]["webhook_listener_ssl_cert"], ca_certs=webhook_listener_ssl_ca_cert, server_side=True)
httpd.serve_forever() # Here we go!