# LiveTradingBots

_A homemade humble library to run automated python crypto trading bots_

\
🛠️ Setup commands (virtual environment included)
-------------
> git clone https://github.com/Youra82/titanbot.git \
> bash titanbot/install.sh

Botausführung:
> bash titanbot/run_envelope.sh

Monitorinstallation:
> chmod +x monitor_bot.sh

Monitorabfrage:
> ./monitor_bot.sh

Backtest & Optimizer benutzen
> ./monitor_bot.sh optimize

Cache vom Optimizer löschen:
> ./monitor_bot.sh clear-cache

Abfrage der letzten Trading-Entscheidungen:

> grep "TRADE_DECISION:" /home/ubuntu/titanbot/logs/envelope.log | tail -n 20

Crontab -e jobs ansehen:
> grep CRON /var/log/syslog | tail -n 20

\
⭐ Bots and strategies
-------------
- **Complete Envelope Bot** : For detailed information on functionality, installation, and access to all our resources, including codes and explanatory videos, please visit our [article](https://robottraders.io/blog/envelope-trading-bot).
_Use run_envelope.sh to run the bot with the virtual environment, either manually or via cron._

- **Bitunix Bot Template** : This is a simple but all rounded bot code template that can be used to build upon. For detailed information on functionality, installation, and access to all our resources, check this [video](https://youtu.be/Xj_hBOU_7Mc).
_Use run_bitunix_template_bot.sh to run the bot with the virtual environment, either manually or via cron. For example, the terminal command from root/home of VPS would be: bash LiveTradingBots/code/run_bitunix_bot_template.sh_

\
✅ Requirements
-------------
Python 3.12.x
\
See [requirements.txt](https://github.com/RobotTraders/LiveTradingBots/blob/main/requirements.txt) for the specific Python packages


\
📃 License
-------------
This project is licensed under the [GNU General Public License](LICENSE) - see the LICENSE file for details.


\
⚠️ Disclaimer
-------------
All this material and related videos are for educational and entertainment purposes only. It is not financial advice nor an endorsement of any provider, product or service. The user bears sole responsibility for any actions taken based on this information, and Robot Traders and its affiliates will not be held liable for any losses or damages resulting from its use. 
