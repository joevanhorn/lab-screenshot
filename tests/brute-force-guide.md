## Objective

In this lab, you will first perform a targeted brute force attack against a single high-value user account to witness how easily a weak password can be compromised. Then, as the administrator, you will neutralize this entire class of attack by implementing a multi-factor authentication (MFA) policy.

## Scenario

*(The Year: **2015**)*

The good news is that TaskVantage is a massive success. The bad news is that this success has attracted unwanted attention. Your Head of Engineering just burst into your office, looking pale.

The account of **one of our longest-serving and most trusted Principal Engineers** has been compromised. The attacker gained access to our most sensitive source code repositories. Luckily, the engineer received a git notification email for a commit they didn't make and was able to sound the alarm before significant damage was done. The breach has been contained, but the "what if" is chilling. What if the attacker had been more stealthy? They could have stolen our entire intellectual property.

A frantic investigation reveals a disturbing and sophisticated pattern in the logs. There was no loud, obvious alarm. Instead, the logs show a **methodical, low-and-slow brute force attack** targeting that one specific engineer's account over the course of several days.

The attacker was smart; they knew about our account lockout policy. Their script was configured to make a few attempts, just below the lockout threshold, and then go silent for a set period before starting again. This "trickle" attack was designed to be almost invisible to our basic automated alerts.

But how did they finally get in? The investigation reveals a subtle vulnerability born from policy fatigue. Having been with the company for years and forced to rotate her password every 90 days, the engineer had fallen into a predictable pattern, moving from *Winter2014\!* to *Spring2015\!*. The patient attacker, having profiled her as a high-value target, simply ran their low-and-slow script, trying these predictable patterns until one eventually succeeded.

The executive team is demanding an immediate fix. It's clear that even with lockout policies in place, a patient attacker can still break through a password-only defense. Your mission is to stop the bleeding by implementing a second factor of authentication that renders this entire attack methodology useless.

## The Attacker

It's time to switch roles. You are no longer the defender of TaskVantage; you are the adversary trying to break in.

Your perspective is one of pure opportunism. You've identified TaskVantage as a prime target: a fast-growing tech company that has likely prioritized features over security. You have also identified your high-value target: a long-tenured Principal Engineer. Your strategy is not one of broad, opportunistic spraying; it is a focused, patient, and targeted assault. Your goal is to use an automated script to run a dictionary of predictable, seasonal passwords against this single account, knowing that their long history of mandatory password rotations has likely created a pattern you can exploit.

It's a numbers game, and the odds are in your favor. Let's begin.

1. From Tech{Camp} \- Brute Force Attack Simulator, **Execute** the attack

[SCREENSHOT: Brute Force Attack Simulator showing the Execute button ready to launch the attack]

2. There has been an account successfully compromised, success\!

[SCREENSHOT: Attack simulator results showing a successfully compromised account]

## The Defender \- Investigation and Control

Take off your attacker hat. The breach was successful. You've proven how easily a simple brute force attack can compromise an employee account at TaskVantage.

Now, put on your **defender** hat. You are the security architect responsible for protecting the company. The executive team is demanding an immediate and effective solution. Your task is to contain this threat and ensure it cannot happen again.

Our work here has three phases: Investigation, Implementing the Control, and Verification.

**Investigation: See the Attack**  
First, log in as an administrator to analyze the system logs. You need to see the attack from the defender's point of view and find the digital breadcrumbs left by the attacker.

1. From the Admin Console, go to **Reports \> System Log.**   
2. On the System Log page review the recent logs showing the string of failed login attempts followed by a successful attempt

[SCREENSHOT: System Log showing failed login attempts followed by a successful login]

### 

**Control Implementation: Implement MFA**  
To address the risk of a successful password based attack Taskvantage has decided to implement MFA for all employees.

From the Admin Console, go to **Security \> Authentication Policies \> App sign-in**

1. Select **TaskVantage \- Apps** 

2. Select **Actions \> Edit** next to the **Employee Access** Rule

3. Review the configuration under **THEN**

4. Adjust the configuration to require MFA. Verify that the following settings are configured:

   

| Setting | Expected Value |
| ----- | ----- |
| THEN Access is  | Allowed after successful authentication |
| AND User must authenticate with | Password \+ Another factor |
| AND Possession factor constraints are | Phishing Resistant **Disabled** Hardware Protected **Disabled** Require user Interaction **Enabled** Any interaction |
| AND Authentication methods | Allow any method that can be used to meet the requirement |
| AND Option to stay signed in | Show after users sign in **Disabled** |

5. Select **Save** and provide MFA to persist the change, if required.

[SCREENSHOT: Authentication policy rule configured with Password plus Another factor for MFA]

**NOTE:** Using MFA will prevent successful authentication to protected applications when an adversary is using password based attacks like Brute Force, Password Spray, and Credential Stuffing.

## The User Experience: Factor Enrollment

You've successfully enabled the MFA policy as an administrator. Now, let's briefly switch perspectives to see the impact of your change on the workforce.

Your employees won't be able to use MFA until they enroll a factor. You will now step into the shoes of Alex, the employee whose account was previously compromised. Your task is to log in as them and complete the mandatory enrollment flow you just created. This is a crucial one-time setup that every employee at TaskVantage will now experience.

1. Copy your Okta org URL {{idp.tenantDomain}}  
2. Launch the virtual desktop with the **Launch** button under Phishing Virtual Environment   
3. On the **User** virtual desktop, open Chrome and paste your Okta org ULR  
4. Log in as *alex.martinez@atko.email* with your password *Spring2026\!*

**NOTE:** Because of the policy you created, you will be immediately prompted to set up Multi-Factor Authentication.

5. Follow the on-screen instructions to set up Okta Verify on your mobile device. 

**NOTE:** This will involve installing the app on your mobile device and scanning a QR code.

6. Once enrollment is complete, you will be successfully logged into Alex's dashboard.

Great. The user is now fully enrolled and protected by MFA. It's time to put your defender hat back on and complete the final, most important step.

## Verification

You've implemented the control, and your user is enrolled. Now you must **verify that the defense works.**

You will now re-run the exact same attack script you used in “**The Attack”**. This is a controlled test to confirm that your new MFA policy effectively neutralizes the password based attacks. 

1. From the Tech{Camp} \- Brute Force Attack Simulator, **Execute** the attack  
2. From the Tech{Camp} \- Brute Force Attack Simulator, observe the unsuccessful attack results

[SCREENSHOT: Attack simulator results showing the attack was unsuccessful after MFA was enabled]

From the Admin Console, go to **Reports \> System Log.** 

3. On the System Log page review the recent logs showing failed password authentication attempts

*Congratulations\! You have successfully investigated a threat, implemented a powerful control, understood the end-user impact, and verified that your company is now secure from brute force attacks like password spray. But is a simple 'Yes/No' push notification truly secure? In the next module, we will see how attackers have adapted to this new defense...*

End of lab
