# Silence Private Server for Astra GPS Module
This is a self-hosted server solution for interfacing with the Astra GPS module of Silence electric scooters. 
This project empowers owners to maintain control over their data, ensuring **privacy** and **independence** from the manufacturer.

This project was developed initially and primarily by **(https://www.linkedin.com/in/andrea-gasparini-a14824143/)[Andrea Gasparini]** @88gaspa88.
We also thank the technical contributions on the **(https://www.elektroroller-forum.de/viewforum.php?f=128)[Elektroroller forum]**

If you like this project you can support us with :coffee: or simply put a :star: to this repository :blush:
<a href="https://www.buymeacoffee.com/lorenzodeluca" target="_blank">
  <img src="https://www.buymeacoffee.com/assets/img/custom_images/yellow_img.png" alt="Buy Me A Coffee" width="150px">
</a>
[![buy me a coffee](https://img.shields.io/badge/support-buymeacoffee-222222.svg?style=flat-square)](https://www.buymeacoffee.com/lorenzodeluca)
> **Warning**
> :warning: This software was developed by analyzing frames from/to Silence Servers, it was not sponsored or officially supported by **Silence**
> If someone from **Silence** would like to contribute or collaborate please contact me at [me@lorenzodeluca.dev](mailto:me@lorenzodeluca.dev?subject=[GitHub]Silence-Private-Server)

### Tested on Silence Scooters
- [x] Silence S01 Connected
- [ ] Testing ongoin on **Seat Mò** and **Silence S01+**

## How It Works
**SilencePrivateServer** acts as a replacement for the Silence server. 
It connects your Silence scooter to **your own private server**, effectively giving you control over your data. Here's a brief overview of how it works:

1. **Astra Module Configuration**: The Astra module parameters need to be modified to connect to your private server instead of the Silence server.
2. **TCP Port Forwarding**: Your scooter connects to your private server through a TCP port. This port needs to be exposed using port forwarding.
3. **Dynamic DNS**: If you don't have a static public IP, you'll need to set up a dynamic DNS.
4. **24/7 Host**: You will need a host that is up and running 24/7 to install the server (Docker, Linux or Windows) and a **MQTT Daemon** in your local network.

Once the Scooter is interfaced with your private server, it **periodically connects** to the server through the exposed port and publishes its **state via MQTT**.

## Configuration of the Astra Module
To configure the Astra module, you need to follow these steps:

1. **TX and RX Connection**: Connect the TX and RX pins as shown in the image below. (Insert image here)
2. **Serial Connection**: Connect to the Astra module via RS232 serial port with connection parameters 115200 baud, 8, N, 1.
3. **Terminal Commands**: Once connected, enter the following commands in the terminal:
    - `$IPAD1, public_ip_address` ,or you Dynamic DNS FQDN
    - Optionally, you can also set the port with `$PORT1,#PORT#`. By default, it is set to **34000** as per Silence configuration.

Please note that you need to replace `public_ip_address` and `#PORT#` with your actual public IP address and desired port number respectively.
