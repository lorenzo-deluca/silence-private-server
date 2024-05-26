# silence-private-server
This is a self-hosted server solution for interfacing with the Astra GPS module of Silence electric scooters. 
This project empowers owners to maintain control over their data, ensuring **privacy** and **independence** from the manufacturer.

## How It Works
**SilencePrivateServer** acts as a replacement for the Silence server. 
It connects your Silence scooter to **your own private server**, effectively giving you control over your data. Here's a brief overview of how it works:

1. **Astra Module Configuration**: The Astra module parameters need to be modified to connect to your private server instead of the Silence server.
2. **TCP Port Forwarding**: Your scooter connects to your private server through a TCP port. This port needs to be exposed using port forwarding.
3. **Dynamic DNS**: If you don't have a static public IP, you'll need to set up a dynamic DNS.
4. **24/7 Host**: You will need a host that is up and running 24/7 to install the server (Docker, Linux or Windows) and a **MQTT daemon** in your local network.

Once the Scooter is interfaced with your private server, it **periodically connects** to the server through the exposed port and publishes its state via MQTT.

## Configuration of the Astra Module
To configure the Astra module, you need to follow these steps:

1. **TX and RX Connection**: Connect the TX and RX pins as shown in the image below. (Insert image here)
2. **Serial Connection**: Connect to the Astra module via RS232 serial port with connection parameters 115200 baud, 8, N, 1.
3. **Terminal Commands**: Once connected, enter the following commands in the terminal:
    - `$IPAD1, public_ip_address` ,or you Dynamic DNS FQDN
    - Optionally, you can also set the port with `$PORT1,#PORT#`. By default, it is set to **34000** as per Silence configuration.

Please note that you need to replace `public_ip_address` and `#PORT#` with your actual public IP address and desired port number respectively.
