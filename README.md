# tableau-server-webhooks-to-slack
This aptly named application is a small server-side Python script that will:

* Create a webhook for you on Tableau Server, for any of the available events
* Listen to said webhook
* When the event occurs, capture the relevant information and post that to Slack

## Architecture

### Components

We're using just a few simple components:

* [TSC](https://github.com/tableau/server-client-python) is going to communicate with the Tableau Server REST API to set up (and later remove) the webhook. It's also going to fetch metadata and a preview image of the workbook at hand.
* [HTTPServer](https://docs.python.org/3/library/http.server.html) is going to be the lightweight web server that listens to the events from the webhook. It's going to be wrapped in an `ssl` socket according to [these instructions](https://blog.anvileight.com/posts/simple-python-http-server/).
* [python-slackclient](https://github.com/slackapi/python-slackclient): some Slack library we used before. Really easy interface to the Slack API which we'll use to post messages.

### Flow

Probably to be illustrated in Lucidchart, but for now:

* Get credentials from the appropriate source (see below).
* Log in to Tableau Server REST API with PAT
* Create webhook with name, event, URL.
* Spin up the "listener web server" that listens to said URL, forever.
  * This HTTP Server has a request handler function to receive and process requests from Tableau Server's webhook
  * It then also takes care of fetching additional metadata for the workbook, and ...
  * ... of posting to Slack when we have everything.
* When the script is terminated with a KeyboardInterrupt, delete the webhook we created previously.

To run on port 443 with a virtualenv: `sudo ./.venv/bin/python tableau_server_webhooks_to_slack.py <arguments>`. This is because in Linux, non-root processes are not allowed to bind to port 443.

## Authentication

We're authenticating against two services or platforms: Tableau Server and Slack. There are three methods of providing the necessary credentials; or rather, tokens. These are:

* As a command line argument. If these are specified, they will take precedence over environment variables. The arguments are:
  * `--pat-name`: Tableau Server Personal Access Token Name for the Tableau Server REST API. See REST API Authentication.
  * `--pat-secret`: Tableau Server Personal Access Token Secret.
  * `--slack-token`: The token for your Slack application (or rather, bot), that has permissions to write and upload to that channel.
* Alternatively, the arguments for the tokens may be omitted from the command line, in which case the script will look for these environment variables (in the same order). To specify these, use the appropriate syntax for your operating system. Consider `export` when using a virtualenv. The variable names:
  * `TS_PAT_NAME`
  * `TS_PAT_SECRET`
  * `SLACK_TOKEN`  
* If neither of the two methods above are used, the script will prompt for the required information.

## Legacy

Parse arguments (to be documented):

parser = argparse.ArgumentParser(prog="tableau-server-webhooks-to-slack", description="Spin up a quick \"webhook listener\" for Tableau Server, that'll post to Slack when it happens. Credentials can be passed as a command line argument, as an environment variable, or entered interactively.")
# Tableau Server REST API arguments
parser.add_argument("--server", dest="server", required=True, type=str, help="The Tableau Server to create the webhook on.")
parser.add_argument("--site", dest="site", required=True, type=str, help="The site on the Server to perform these actions on. Default site is \"\" (for reasons).")
parser.add_argument("--pat-name", dest="pat_name", required=False, type=str, help="Name of the Personal Access Token for the Tableau Server REST API. See REST API Authentication. Environment variable alternative: TS_PAT_NAME.")
parser.add_argument("--pat-secret", dest="pat_secret", required=False, default=os.getenv("TS_PAT_SECRET"), type=str, help="Secret or value of the Personal Access Token for the Tableau Server REST API. See REST API Authentication. Environment variable alternative: TS_PAT_SECRET.")
parser.add_argument("--ssl-certificates", dest="ssl_certificates", required=False, type=str, help="If applicable, path to SSL certificates file for verification (e.g. when self-signed certificates are in use).")
# Webhooks arguments
parser.add_argument("--webhook-event-name", dest="webhook_event_name", required=True, type=str, help="The name of the webhook event as listed in the available trigger events. Shortened version e.g. \"workbook-created\".")
parser.add_argument("--webhook-name", dest="webhook_name", required=True, type=str, help="The name of the webhook itself, simply for reference.")
parser.add_argument("--webhook-url", dest="webhook_url", required=True, type=str, help="The URL the webhook will call to, which this server should listen to! We suggest something like: \"https://appserver.biztory.com:4443/tableau-server-webhooks-workbook-created\". The path will be checked against when getting a POST request (from the webhook), and the port should match")
parser.add_argument("--webhook-listener-port", dest="webhook_listener_port", required=True, type=int, help="The port on which our server listener on this side, will listen to Tableau Server's webhook requests. Should match with the webhook-url above.")
parser.add_argument("--webhook-listener-ssl-cert", dest="webhook_listener_ssl_cert", required=True, type=str, help="The location of the SSL certificate for our listener.")
parser.add_argument("--webhook-listener-ssl-key", dest="webhook_listener_ssl_key", required=True, type=str, help="The location of the SSL key for our listener.")
parser.add_argument("--webhook-listener-ssl-ca-cert", dest="webhook_listener_ssl_ca_cert", required=False, type=str, help="The chain or Certificate Authority certs.")
# Slack arguments
parser.add_argument("--slack-workspace", dest="slack_workspace", required=True, type=str, help="Name of the workspace to post to e.g. \"biztory\".")
parser.add_argument("--slack-channel", dest="slack_channel", required=True, type=str, help="URL \"code\" of the slack channel to post to e.g. \"C123456\".")
parser.add_argument("--slack-token", dest="slack_token", required=False, default=os.getenv("SLACK_TOKEN"), type=str, help="The token for your Slack application (or rather, bot), that has permissions to write and upload to that channel. Environment variable alternative: SLACK_TOKEN.")
