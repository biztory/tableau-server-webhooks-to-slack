# tableau-server-webhooks-to-slack

This descriptively named application is a small server-side Python script that will:

* Create webhooks for you on Tableau Server, for any of the events supported.
* Listen to said webhooks.
* When the event occurs, capture the relevant information and post that to Slack.

Potential/planned improvements:

* ~Handle [all events](https://github.com/tableau/webhooks-docs#events) rather than just workbook created. This probably boils down to first, creating a webhook for each event type (with the same URL/endpoint), and second, adjust how we process a response to handle all cases generically.~ This is done.
* Extend to work with platforms other than Slack, including e.g. Teams.

## Architecture

### Components

We're using just a few simple components:

* [Tableau Server Client (TSC)](https://github.com/tableau/server-client-python) is going to communicate with the Tableau Server REST API to set up (and later remove) the webhook. It's also going to fetch metadata and a preview image of the workbook at hand.
* [HTTPServer](https://docs.python.org/3/library/http.server.html) is going to be the lightweight web server that listens to the events from the webhook. It's going to be wrapped in an `ssl` socket according to [these instructions](https://blog.anvileight.com/posts/simple-python-http-server/).
* [python-slackclient](https://github.com/slackapi/python-slackclient): some Slack library we used before. Really easy interface to the Slack API which we'll use to post messages.

### Flow

![A diagram of the architecture of this solution.](./doc/tableau-server-webhooks-to-slack_-_Architecture.png "tableau-server-webhooks-to-slack - Architecture")

* Get credentials from the appropriate source (see below).
* Log in to Tableau Server REST API with PAT (diagram, step 1).
* Create each webhook with name, event, URL (diagram, step 2).
* Spin up the "listener web server" that listens to said URL, forever (diagram, HTTPServer component).
  * This HTTP Server has a request handler function to receive and process requests from Tableau Server's webhook (diagram, step 3).
  * It then also takes care of fetching additional metadata for the workbook (diagram, step 4), and ...
  * ... of posting to Slack when we have everything (diagram, step 5).
* When the script is terminated with a KeyboardInterrupt, delete the webhooks we created previously.

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

For Slack, this was developed using a [bot token](https://api.slack.com/authentication/token-types#bot).

## Usage

### Getting started

* Clone this repository
* Create a Python [virtualenv](https://docs.python-guide.org/dev/virtualenvs/) and activate it.
* Install the requirements with pip: `python -m pip install -r requirements.txt`.
* Provide all the necessary information in `config.ini`:
  * Tableau Server section:
    * server: the URL for Tableau Server, including the protocol (http or https) and without trailing slash.
    * site: the ID from the URL, blank if Default.
    * pat_name, pat_secret: see the Authentication section above.
    * ssl_certificates: the path to a certificate or certificate chain, in case the SSL certificate for your Tableau Server is self-signed, or internally signed and not trusted by the regular CAs.
  * ~Webhook Configuration section: these are the three parameters passed to the Tableau Server REST API to [create a webhook](https://github.com/tableau/webhooks-docs#curl).~ These are now aprt of the script and have fixed names, except the webhook_url setting.
  * Listener section: this is the HTTPServer component we'll be spinning up. It requires a free port on your server, as well as a certificate and key (and chain) for HTTPS. It doesn't really matter how exactly you obtain these ([certbot](https://certbot.eff.org/) is an option), but they're required as Tableau Server webhooks only reach out to secure endpoints.
  * Slack section:
    * slack_workspace: your workspace's name.
    * slack_channel: the channel ID to post to, which you can also derive from Slack in your browser.
* Run it: `python tableau_server_webhooks_to_slack.py`.

### Other usage remarks

* To run on port 443 with a virtualenv: `sudo ./.venv/bin/python tableau_server_webhooks_to_slack.py <arguments>`. This is because in Linux, non-root processes are not allowed to bind to port 443.
* As a service with systemd, which is ideal... following [these instructions](https://tecadmin.net/setup-autorun-python-script-using-systemd/).
  * We'll just save the `.service` file to GitHub, in our application directory, and symlink it in `/lib/systemd/system/`.
  * Start, stop, status with `sudo systemctl start tableau-server-webhooks-to-slack.service`.
  * We'll also want to make sure we run python with stdout unbuffered, so the output appears in the journal immediately. `python -u file.py`.
  * View logs with `sudo journalctl -e -u tableau-server-webhooks-to-slack.service`.
