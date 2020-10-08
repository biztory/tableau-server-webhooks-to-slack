import getpass # for the TS and Slack tokens if they're not provided
import configparser # to parse config.ini
import tableauserverclient as TSC # for setting up the webhook on Tableau Server and getting metadata
from http.server import HTTPServer, BaseHTTPRequestHandler # for listening to the webhook
import ssl # for "encapsulating" HTTPServer
import re # for matching the listener URL path
from io import BytesIO # for the responses we send
import json # for parsing
import slack # for obvious reasons
import os # because we write the thumbnail of e.g. a new workbook to a temp file, and so we can remove it

#### Parse config file
config = configparser.ConfigParser()
config.read("config.ini")

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

#### Create webhook
print("Creating webhook")

webhook_model = TSC.WebhookItem()
webhook_model.name = config["Webhook Configuration"]["webhook_name"]
webhook_model.url = config["Webhook Configuration"]["webhook_url"]
webhook_model.event = config["Webhook Configuration"]["webhook_event_name"]

try:
    webhook = tableau_server.webhooks.create(webhook_model)
    print("Webhook created: \"" + webhook.name + "\" with id " + webhook._id)
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
print("Starting HTTPServer for our listener application.")
try:
    listener_path = re.search("\.[\w]+\:?[\d]{0,5}(\/[\w\d\-\_]+)", config["Webhook Configuration"]["webhook_url"]).group(1)
    print("Found listener path \"" + listener_path + "\" in URL.")
except Exception as e:
    print("Couldn't extract a path from the URL: " + config["Webhook Configuration"]["webhook_url"])

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
            resource_luid = body_json["resource_luid"]
            site_luid = body_json["site_luid"]
            event_type = body_json["event_type"]
            created_at = body_json["created_at"]
            print("Received event \"" + event_type + "\" for resource " + resource_name)

            # We should have templates for what type of event and how we're going to share them in Slack, e.g. a screenshot or some other metadata...
            # For now, let's just handle the event workbook-created

            print("Getting new workbook's metadata and thumbnail from Tableau Server.")

            try:
                
                # Sign in again, get the metadata, etc.
                tableau_server.auth.sign_in_with_personal_access_token(ts_auth)
                workbook = tableau_server.workbooks.get_by_id(resource_luid)
                workbook_owner = tableau_server.users.get_by_id(workbook.owner_id)._name
                workbook_url = re.sub("(https?:\/\/[\d\w\-\_\.]+)\/", config["Tableau Server"]["server"] + "/", workbook.webpage_url)
                tableau_server.workbooks.populate_preview_image(workbook)
                workbook_image = workbook.preview_image

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

                    slack_message_text = workbook_owner + " just published a new workbook to our Tableau Server! It's titled <" + workbook_url + "|*" + resource_name + "*> and here is what it looks like."

                    # Get info about the channel first, see if we need to join it.
                    if not slack_web_client.conversations_info(channel=config["Slack"]["slack_channel"])["channel"]["is_member"]:
                        print("Not a member yet, so joining the channel.")
                        slack_web_client.conversations_join(channel=config["Slack"]["slack_channel"])

                    # Post!
                    try:
                        print("Uploading to Slack!")
                        upload_response = slack_web_client.files_upload(file=temp_file, channels=config["Slack"]["slack_channel"], initial_comment=slack_message_text) 
                        # print(upload_response)
                    except slack.errors.SlackApiError as e:
                        print(e.response)

                    # Remove the file after we are done.
                    os.remove(temp_file)

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

# We'll use a try-except structure here that catches KeyboardInterrupt to allow "graceful" shutdown with CTRL+C

try:
    httpd = HTTPServer(("", int(config["Listener"]["webhook_listener_port"])), SimpleHTTPRequestHandler)
    httpd.socket = ssl.wrap_socket(httpd.socket, keyfile=config["Listener"]["webhook_listener_ssl_key"], certfile=config["Listener"]["webhook_listener_ssl_cert"], ca_certs=webhook_listener_ssl_ca_cert, server_side=True)
    httpd.serve_forever() # Here we go!

except KeyboardInterrupt:
    print("Received KeyboardInterrupt")

    # Sign in to Tableau Server again, because our session from before is probably expired
    print("Signing in to Tableau Server with token name \"" + pat_name + "\".")
    try:
        tableau_server.auth.sign_in_with_personal_access_token(ts_auth)
    except Exception as e:
        print("Failed to sign in to Tableau Server. Quitting. The webhook will still exist there.")
        print(e)
        exit(1)

    #### Delete the webhook when we shut down (or right now for testing purposes)
    print("Deleting webhook")
    try:
        tableau_server.webhooks.delete(webhook._id)
    except Exception as e:
        print("Failed to delete webhook. Leaving it there...")
        print(e)

    #### Sign out of Tableau Server once the Webhook has been removed
    print("Signing out of Tableau Server.")
    try:
        tableau_server.auth.sign_out()
    except:
        print("Failed to sign out of Tableau Server. But we're at the end of the script.")
    
    # raise # Then we quit with the error? Or should we just do nothing?