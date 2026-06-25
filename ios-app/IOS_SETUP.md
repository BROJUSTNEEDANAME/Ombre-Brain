# 把「家」装成 iPhone 原生 app（用 Mac）

装一次，以后我每次改完一部署，你 app 里**自动更新**，不用再重装。
装好之后**没有任何 Safari 工具栏 / 浏览器痕迹**，全屏，跟真 app 一样。

> 不用怕，照着一步步来。哪一步报错、卡住，把屏幕截给我，我陪你解。

---

## 你需要准备

- 一台 **Mac**
- 你的 **iPhone** + 一根能连电脑的**数据线**
- 一个 **Apple ID**（普通的就行，免费）

> 说明：免费 Apple ID 装的 app **7 天后会过期**，到时在 Xcode 里再点一次运行就续上了。
> 嫌麻烦可以加入 Apple 开发者计划（99 美元/年），装的能用一年。先用免费的就够。

---

## 第一步：装好工具（每台 Mac 只做一次）

1. **装 Xcode**：打开 Mac 上的 **App Store**，搜 `Xcode`，点安装（很大，几个 G，耐心等）。
   装完打开一次 Xcode，同意条款。
2. **装 Node.js**：浏览器打开 <https://nodejs.org>，下载 **LTS** 版本，双击安装。
3. **装 CocoaPods**：打开「**终端**」（聚焦搜索 Terminal），粘贴回车：
   ```
   sudo gem install cocoapods
   ```
   （会让你输 Mac 开机密码，输的时候屏幕不显示是正常的，输完回车。）

---

## 第二步：拿到项目

在终端里依次粘贴、回车：

```
git clone https://github.com/brojustneedaname/ombre-brain.git
cd ombre-brain/ios-app
npm install
npx cap add ios
```

> 如果 `git clone` 提示已存在，就直接 `cd ombre-brain && git pull && cd ios-app`。

---

## 第三步：用 Xcode 打开并装到手机

1. 终端里跑：
   ```
   npx cap open ios
   ```
   会自动打开 **Xcode**。
2. 用数据线把 **iPhone 连上 Mac**，手机上如果弹「信任此电脑」点信任。
3. 在 Xcode 左上角，把运行目标从模拟器改成**你的 iPhone**（点那个设备名下拉选）。
4. 左侧点最上面的 **App**（蓝色图标）→ 中间选 **Signing & Capabilities** 标签：
   - 勾上 **Automatically manage signing**
   - **Team**：点下拉 → **Add an Account**，登录你的 Apple ID，然后选它
   - 如果报 Bundle Identifier 冲突，把 **Bundle Identifier** 改成独一无二的，比如
     `com.你的名字.ombrehome`
5. 点左上角的 **▶（运行）** 按钮，等它编译、装到你手机上。

---

## 第四步：在 iPhone 上信任它

第一次装，iPhone 上点开会提示「不受信任的开发者」。去：

**设置 → 通用 → VPN 与设备管理 →** 点你的 Apple ID →「**信任**」

然后回桌面点开「家」，就能用了。全屏，没有工具栏。🎉

---

## 以后怎么更新？

**不用做任何事。** 这个 app 是直接加载线上版的，我改完一部署，你下次打开 app 就是最新的。
（只有那张「7 天过期」要留意：过期了就把 iPhone 连 Mac，Xcode 里再点一次 ▶ 就行。）

---

## 卡住了？常见问题

- **`npx cap add ios` 报 CocoaPods 相关错** → 回第一步把 CocoaPods 装上。
- **Xcode 编译报签名/Provisioning 错** → 回第三步第 4 点，确认勾了自动签名、选了 Team、Bundle Identifier 独一无二。
- **手机上点开闪退或白屏几十秒** → 服务器在冷启动（睡醒要几十秒），等一下；或确认手机有网。
- **其它任何报错** → 截图发我，告诉我卡在第几步，我帮你看。
