# CS2-Demo-Stats
自用项目，主要是用python html实现记录cs2本地服务器上的bot数据并用社区公开的rating公式计算出来并保存，搭配https://github.com/ed0ard/CS2-Bot-Improver  使用游玩体验会更好

luzhi.bat主要用于在游戏目录cfg文件创建一个cfg文件并保持每隔一段时间覆写，覆写内容则是使用cstv录制指令，名字则为取当前时间，此举在于保证每个demo名字不相同以避免demo覆盖。
需要自己编辑luzhi.bat文件改写游戏路径，以文本形式更改。如图<img width="1084" height="153" alt="屏幕截图 2026-06-15 225220" src="https://github.com/user-attachments/assets/6f92a514-d17a-40ae-b6ec-87a923e2b7b3" />
server.cfg文件则放入游戏cfg文件夹覆盖，以保证cstv处于活跃状态。
start_cs2.bat文件同样需要修改游戏路径，方法同上。主要用于方便的启动游戏，或者可以自己在cs2启动项添加：-insecure +exec server.cfg  -disable_workshop_command_filtering                            
使用方式：运行luzhi.bat，使用start_cs2.bat进入或者启动项进入游戏主界面后选择练习或者创意工坊地图，选择竞技模式，安装了bot-improve的话，需要移除所有bot，然后自己输入指令添加完bot输入exec luzhi ，就会开始录制demo，demo文件自动保存在csgo文件夹里的gotv文件夹里面，进入程序网页后，把gotv文件路径放进扫描路径里，关键字可取可不取，点击开始扫描，就会持续监控demo文件，自动上传到网页，直到点击停止或者进行其他操作。目前仅支持有steam 64位id存在的bot，其余会导致数据统计混乱。    

本程序不会导致vac因为已经开启了-insecure，不会在有vac保护的服务器下进行。

效果如图所示<img width="2307" height="1203" alt="屏幕截图 2026-06-15 232254" src="https://github.com/user-attachments/assets/3224d612-e183-4f9b-91c1-cec2a9be14d2" />
<img width="2350" height="1189" alt="屏幕截图 2026-06-15 232410" src="https://github.com/user-attachments/assets/3f3aeabe-0d73-4fad-a9dd-33de313b7b3b" />
