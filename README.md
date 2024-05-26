# Silence Private Server for Astra GPS Module
This is a self-hosted server solution for interfacing with the Astra GPS module of Silence electric scooters.\
This project empowers owners to maintain control over their data, ensuring **privacy** and **independence** from the manufacturer.

This project was developed initially and primarily by [**Andrea Gasparini**](https://www.linkedin.com/in/andrea-gasparini-a14824143) @88gaspa88 (88gaspa88@gmail.com).\
We also thank the technical contributions on the [Elektroroller forum](https://www.elektroroller-forum.de/viewforum.php?f=128)

If you like this project you can support us with :coffee: or simply put a :star: to this repository :blush:
<a href="https://www.buymeacoffee.com/lorenzodeluca" target="_blank">
  <img src="https://www.buymeacoffee.com/assets/img/custom_images/yellow_img.png" alt="Buy Me A Coffee" width="150px">
</a>

[![buy me a coffee](https://img.shields.io/badge/support-buymeacoffee-222222.svg?style=flat-square)](https://www.buymeacoffee.com/lorenzodeluca)
> **Warning**
> :warning: This software was developed by analyzing frames from/to Silence Servers, it was not sponsored or officially supported by **Silence**
> If someone from **Silence** would like to contribute or collaborate please contact me at [me@lorenzodeluca.dev](mailto:me@lorenzodeluca.dev?subject=[GitHub]Silence-Private-Server)

## Contents
- [How it works](#how-it-works)
- [Astra Module Configuration](#astra-module-configuration)
- [Installation](#installation)
- [MQTT Integration](#mqtt-integration)
- [Support](#support)

### Tested on Silence Scooters
- [x] Silence S01 Connected
- [ ] Testing ongoin on **Seat Mò** and **Silence S01+**

## How It Works
**SilencePrivateServer** acts as a replacement for the Silence server.\
It connects your Silence scooter to **your own private server**, effectively giving you control over your data.\
Here's a brief overview of how it works:

1. **Astra Module Configuration**: The Astra module parameters need to be modified to connect to **your private server instead of the Silence server**.
2. **TCP Port Forwarding**: Your scooter connects to your private server through a **TCP port**. This port needs to be **exposed using port forwarding**.
3. **Dynamic DNS**: If you don't have a static public IP, you'll need to set up a dynamic DNS.
4. **24/7 Host**: You will need a host that is up and running 24/7 to install the server (Docker, Linux or Windows) and a **MQTT Daemon** in your local network.

Once the Scooter is interfaced with your private server, it **periodically connects** to the server through the exposed port and publishes its **state via MQTT**.\
It will also be possible to send commands to the scooter like Silence App: Power On, Power Off, Open Seat, Blink Lights and Horn

### Astra Module Configuration
To configure the Astra module, you need to follow these steps:

1. **TX and RX Connection**: Connect the TX and RX pins as shown in the image below. ![Pinout](images/pinout.png) 
2. **Serial Connection**: Connect to the Astra module via **RS232 serial port** with connection parameters **115200 baud, 8, N, 1**.
3. **Terminal Commands**: Once connected, enter the following commands in the terminal:
    - `$IPAD1, #PUBLIC_IP#` , change **#PUBLIC_IP** with your IP or Dynamic DNS FQDN
    - Optionally, you can also set the port with `$PORT1,#PORT#`. By default, it is set to **38955** as Silence Server.

Please note that you need to replace `#PUBLIC_IP#` and `#PORT#` with your actual public IP address and desired port number respectively.

## Installation
Once clone the project, you need to configure and running the server, follow these steps:

### Determine Your IMEI
First, you need to determine your IMEI. \
You can do this by going to the Silence app, selecting "My Vehicles", choosing your scooter, and then going to "**Technical Sheet**".
<img src="images/SilenceApp-IMEI.png" alt="IMEI" height="400" />

### Create Configuration File
Next, copy the '**configuration.template.json**' file and create a new file named '**configuration.json**'. \
In this file, specify the following parameters:
- `IMEI`: Enter the IMEI of your Astra module, which you determined in the previous step.
- `ServerPort`: If you modified this in the module configuration, enter the new value. Otherwise, leave it as the default 38955.
- `bridgeMode`: If set to true, the server will still send data to the Silence server, allowing the Silence app to function normally.
    If you do not want to send data to Silence (which will cause the app to stop working), set this to FALSE.
- `MQTT broker`: Configure **port**, **user**, **pass** of your local MQTT Broker.

## Running the Server
Once you have configured your 'configuration.json' file, you can run the server. Here are the steps:

### Running as Script
1. **Install Python Libraries**: First, install the necessary Python libraries by running the following command in your terminal:
    ```shell
    pip install -r requirements.txt
    ```
2. **Run as a Script**: You can run the server as a script. To do this, navigate to the directory containing your server script and run it using Python.
    ```shell
    python silence-server.py
    ```

### Run as a Docker Container
Alternatively, you can run the server as a Docker container. To do this, you need to build a Docker image and map the 'configuration.json' file.\
Command to build the Docker image:
  ```shell
  docker build -t silence-private-server .
  ```
And here's the command to run the Docker container, mapping the '**configuration.json**' file:
  ```shell
    docker run
    --name silence-server
    --detach --restart unless-stopped 
    --publish **#PORT#**:**#PORT#** 
    --v **local_configuration.json**:/app/configuration.json:ro 
    silence-private-server 
  ```

Here's an example of logs of a running server in Docker, with script the output is the same.
<img src="images/silence-server-docker.png" alt="IMEI" height="400" />

## MQTT Integration
After properly Module and Server configuration you have your own server and you will have your data locally.\
There you can integrate with any MQTT-compatible system; we will soon publish a sample configuration for Home Assistant.  :)

### Status Publishing
Server publishes the scooter's status to an MQTT topic every time it receives a message from the scooter. The base topic is defined in the configuration file (TopicPrefix), and the status is published to a topic composed as follows: `TopicPrefix/IMEI/status`.
The topic name definition is defined in file **scooter_status_definition.json**, but you can subscribe on status/# topic and see data.

Here's a brief overview of how it works:

1. **MQTT Publishing**: The server is set up to publish to an MQTT topic. MQTT is a lightweight messaging protocol for small sensors and mobile devices, optimized for high-latency or unreliable networks.
2. **Topic Structure**: The MQTT topic to which the server publishes is composed of the base topic, **TopicPrefix** parameter, the IMEI of the scooter, and the word 'status'. \
   So, if your TopicPrefix is 'MyScooter' and your IMEI is '123456789', the server would publish to the topic 'MyScooter/123456789/status'.

### Sending Commands
You can send commands to the scooter by publishing to an MQTT topic. The topic is composed of the base topic (TopicPrefix), the IMEI of the scooter, and the word 'command'. So, if your TopicPrefix is 'MyScooter' and your IMEI is '123456789', you would publish commands to the topic 'MyScooter/123456789/command'.

Here are the possible commands you can send:

- **TURN_ON_SCOOTER**: Turns on the scooter.
- **TURN_OFF_SCOOTER**: Turns off the scooter.
- **OPEN_SEAT**: Opens the seat of the scooter.
- **FLASH**: Activates the scooter's flash.
- **BEEP_FLASH**: Activates the scooter's beep and flash.
- **STOP_FLASH**: Stops the scooter's flash.

To send a command, simply publish a message, with an empty payload, to the appropriate MQTT topic. \
For example, to turn on the scooter, you would publish 'TURN_ON_SCOOTER' to 'MyScooter/123456789/command'.

## Support
If you encounter any issues or have questions regarding the integration, please open an issue on this GitHub repository, and I will be happy to assist you.
You can write to me at [me@lorenzodeluca.dev](mailto:me@lorenzodeluca.dev?subject=[GitHub]silence-private-server)

## Contributing
Contributions to the project are welcome! Please fork the repository, make your changes, and submit a pull request.
Any help is welcome, if you have new implementations feel free to make pull requests :blush:
   
## License
GNU AGPLv3 © [Lorenzo De Luca][https://lorenzodeluca.dev]
