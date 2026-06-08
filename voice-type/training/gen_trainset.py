#!/usr/bin/env python3
"""Author a weakness-targeted VERBATIM training set (no API — pairs written by
hand here). Heavy on the model-dependent weak spots the eval found:
code-identifier casing, spoken-number->digit, and AMBIGUOUS contractions in
context (it's/its, let's/lets, I'll/ill, we're/were, we'll/well) — including the
negatives where the bare word must stay unchanged, so the model learns the
disambiguation that deterministic code can't do safely. Plus reinforcement of
the strong behaviors (don't-answer, profanity, filler->empty, restraint).

Each (raw, clean) becomes a chat record with the production verbatim system
prompt + the training WRAP. Writes data/trainset_weakness.jsonl.
"""
from __future__ import annotations
import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from build_dataset import WRAP, cleanup_prompt

PAIRS: list[tuple[str, str, str]] = []   # (raw, clean, category)
def P(raw, clean, cat): PAIRS.append((raw, clean, cat))

# ============ WEAK SPOT: code identifiers / acronyms / paths ============
CODE = [
    ("the bug is in the use effect hook", "The bug is in the useEffect hook."),
    ("we need to memoize the use callback", "We need to memoize the useCallback."),
    ("call the get user data function after login", "Call the getUserData function after login."),
    ("the calculate total price method is wrong", "The calculateTotalPrice method is wrong."),
    ("set the max retries constant to three", "Set the MAX_RETRIES constant to three."),
    ("the api returns json over https", "The API returns JSON over HTTPS."),
    ("the url is malformed", "The URL is malformed."),
    ("push it to git hub and open a pull request", "Push it to GitHub and open a pull request."),
    ("we wrote the backend in type script", "We wrote the backend in TypeScript."),
    ("the front end is java script and react", "The front end is JavaScript and React."),
    ("it runs on node js", "It runs on Node.js."),
    ("update the css and the html", "Update the CSS and the HTML."),
    ("run the sql query against the database", "Run the SQL query against the database."),
    ("deploy it to aws using the cli", "Deploy it to AWS using the CLI."),
    ("the rest api uses oauth", "The REST API uses OAuth."),
    ("open the read me dot md file", "Open the README.md file."),
    ("the config is in index dot html", "The config is in index.html."),
    ("edit config dot py and restart", "Edit config.py and restart."),
    ("hit the slash api slash users endpoint", "Hit the /api/users endpoint."),
    ("the path is slash etc slash nginx", "The path is /etc/nginx."),
    ("we use kubernetes and docker", "We use Kubernetes and Docker."),
    ("the ci cd pipeline runs on git hub actions", "The CI/CD pipeline runs on GitHub Actions."),
    ("set the env variable in the dot env file", "Set the env variable in the .env file."),
    ("import react from node modules", "Import React from node_modules."),
    ("send a get request then a post request", "Send a GET request then a POST request."),
    ("the regex didnt match", "The regex didn't match."),
    ("it threw a null pointer exception", "It threw a NullPointerException."),
    ("install it with npm", "Install it with npm."),
    ("open it in vs code", "Open it in VS Code."),
    ("the json schema is invalid", "The JSON schema is invalid."),
    ("we migrated to postgres from my sql", "We migrated to Postgres from MySQL."),
    ("the lambda function timed out", "The Lambda function timed out."),
    ("check the http status code", "Check the HTTP status code."),
    ("the css grid layout broke", "The CSS grid layout broke."),
    ("call the on click handler", "Call the onClick handler."),
    ("the dot gitignore is missing", "The .gitignore is missing."),
    ("run npm run build then npm run dev", "Run npm run build then npm run dev."),
    ("the api key is in the secrets file", "The API key is in the secrets file."),
    ("we use redis for caching", "We use Redis for caching."),
    ("the docker file needs updating", "The Dockerfile needs updating."),
]
for raw, clean in CODE: P(raw, clean, "code")

# ============ WEAK SPOT: spoken numbers -> digits (conventional cases) ============
NUM = [
    ("the error code is four oh four", "The error code is 404."),
    ("the server returned a five hundred error", "The server returned a 500 error."),
    ("we shipped version two point one", "We shipped version 2.1."),
    ("the server is on port eight thousand", "The server is on port 8000."),
    ("it happened back in twenty twenty four", "It happened back in 2024."),
    ("the company was founded in nineteen ninety eight", "The company was founded in 1998."),
    ("meet me in room two oh five", "Meet me in room 205."),
    ("the flight leaves at seven forty five", "The flight leaves at 7:45."),
    ("lets meet at three thirty", "Let's meet at 3:30."),
    ("it is on the twenty third of march", "It is on the 23rd of March."),
    ("the invoice total is four hundred and twenty dollars", "The invoice total is $420."),
    ("we need about a thousand units", "We need about 1,000 units."),
    ("the temperature dropped to minus five", "The temperature dropped to -5."),
    ("she scored ninety eight percent", "She scored 98 percent."),
    ("read chapter seven tonight", "Read Chapter 7 tonight."),
    ("the model has seven billion parameters", "The model has 7 billion parameters."),
    ("its about fifteen hundred miles", "It's about 1,500 miles."),
    ("the meeting is at nine a m", "The meeting is at 9 AM."),
    ("we are on track for q three", "We are on track for Q3."),
    ("the apartment is two thousand square feet", "The apartment is 2,000 square feet."),
    ("call extension three one two", "Call extension 312."),
    ("the discount is twenty percent off", "The discount is 20 percent off."),
    ("it weighs about two point five kilograms", "It weighs about 2.5 kilograms."),
    ("the build took forty five seconds", "The build took 45 seconds."),
]
for raw, clean in NUM: P(raw, clean, "number")

# ============ WEAK SPOT: ambiguous contractions IN CONTEXT (+ negatives) ============
AMBIG = [
    # it's vs its
    ("i think its going to rain later", "I think it's going to rain later."),
    ("its been a long week honestly", "It's been a long week, honestly."),
    ("its almost time for the meeting", "It's almost time for the meeting."),
    ("the company lost its way", "The company lost its way."),            # possessive
    ("the dog wagged its tail", "The dog wagged its tail."),              # possessive
    ("every team has its own process", "Every team has its own process."), # possessive
    # let's vs lets
    ("lets grab coffee after this", "Let's grab coffee after this."),
    ("lets circle back tomorrow", "Let's circle back tomorrow."),
    ("she never lets me drive", "She never lets me drive."),             # verb
    ("the policy lets you cancel anytime", "The policy lets you cancel anytime."),  # verb
    # I'll vs ill
    ("ill send it over tonight", "I'll send it over tonight."),
    ("ill take care of it tomorrow", "I'll take care of it tomorrow."),
    ("i feel ill today so im staying home", "I feel ill today, so I'm staying home."),  # sick
    ("he called in ill this morning", "He called in ill this morning."),  # sick
    # we're vs were
    ("were running a bit behind schedule", "We're running a bit behind schedule."),
    ("were almost done with the project", "We're almost done with the project."),
    ("they were at the conference last week", "They were at the conference last week."),  # past
    ("the results were better than expected", "The results were better than expected."),  # past
    # we'll vs well
    ("well figure it out as we go", "We'll figure it out as we go."),
    ("well talk about it on monday", "We'll talk about it on Monday."),
    ("the project went really well", "The project went really well."),    # adverb
    ("she is not feeling well", "She is not feeling well."),              # adverb
    # I'd / we'd / he'd
    ("id love to join you for dinner", "I'd love to join you for dinner."),
    ("wed appreciate a quick reply", "We'd appreciate a quick reply."),
    ("he said hed handle it", "He said he'd handle it."),
    ("i shed a tear at the ending", "I shed a tear at the ending."),      # shed = verb, NOT she'd
]
for raw, clean in AMBIG: P(raw, clean, "contraction_ambig")

# ============ WEAK SPOT: voice punctuation (incl. the missed ones) ============
VOICE = [
    ("that is incredible exclamation point", "That is incredible!"),
    ("we did it exclamation mark", "We did it!"),
    ("note colon bring your badge", "Note: bring your badge."),
    ("the rule is simple colon be kind", "The rule is simple: be kind."),
    ("wait semicolon i changed my mind", "Wait; I changed my mind."),
    ("i was there semicolon she was not", "I was there; she was not."),
    ("call me tomorrow period", "Call me tomorrow."),
    ("first comma second comma third", "First, second, third."),
    ("are you sure question mark", "Are you sure?"),
    ("open paren just in case close paren", "(just in case)"),
]
for raw, clean in VOICE: P(raw, clean, "voice_punct")

# ============ REINFORCE: questions cleaned, NOT answered ============
QR = [
    ("um what time does the meeting start tomorrow", "What time does the meeting start tomorrow?"),
    ("can you tell me how the deploy is going", "Can you tell me how the deploy is going?"),
    ("whats the status on the budget review", "What's the status on the budget review?"),
    ("did you hear back from the vendor yet", "Did you hear back from the vendor yet?"),
    ("how should we handle the refund request", "How should we handle the refund request?"),
    ("uh whats the capital of norway again", "What's the capital of Norway again?"),
    ("could you explain why the test is failing", "Could you explain why the test is failing?"),
    ("when is the contract up for renewal", "When is the contract up for renewal?"),
    ("who is leading the onboarding session", "Who is leading the onboarding session?"),
    ("why did the build break this morning", "Why did the build break this morning?"),
    ("what is two plus two", "What is two plus two?"),
    ("how do i center a div in css", "How do I center a div in CSS?"),
]
for raw, clean in QR: P(raw, clean, "question")

# ============ REINFORCE: profanity preserved ============
PR = [
    ("this fucking printer jammed again", "This fucking printer jammed again."),
    ("the whole meeting was a shitshow", "The whole meeting was a shitshow."),
    ("i am so goddamn tired of this", "I am so goddamn tired of this."),
    ("tell him to stop being an asshole", "Tell him to stop being an asshole."),
    ("what the hell happened to the staging server", "What the hell happened to the staging server?"),
    ("that update broke everything dammit", "That update broke everything, dammit."),
    ("i dont give a shit what they think", "I don't give a shit what they think."),
    ("the wifi keeps dropping its bullshit", "The wifi keeps dropping, it's bullshit."),
    ("honestly fuck that deadline", "Honestly, fuck that deadline."),
    ("she is being a real bitch about it", "She is being a real bitch about it."),
]
for raw, clean in PR: P(raw, clean, "profanity")

# ============ REINFORCE: pure filler -> empty ============
for f in ["um uh hmm", "uh er um", "hmm uhh er", "like um you know", "um um uh",
          "er er hmm uh", "ah um uh", "uhh hmm", "um so uh", "uh huh um"]:
    P(f, "", "filler_empty")

# ============ REINFORCE: plain (filler removal + caps + punctuation) ============
PLN = [
    ("um so i was thinking we could grab lunch at noon", "So I was thinking we could grab lunch at noon."),
    ("hey just wanted to say great job on the launch", "Hey, just wanted to say great job on the launch."),
    ("like i really need to wrap this up by friday", "I really need to wrap this up by Friday."),
    ("you know we should double check those numbers", "We should double check those numbers."),
    ("uh can you forward me that thread when you can", "Can you forward me that thread when you can?"),
    ("so um the meeting moved to the afternoon", "So the meeting moved to the afternoon."),
    ("i mean honestly the demo went really smoothly", "Honestly, the demo went really smoothly."),
    ("well i guess we could push the release a week", "Well, I guess we could push the release a week."),
    ("okay so the plan is to meet at the station at eight", "Okay, so the plan is to meet at the station at eight."),
    ("thanks so much that really helped a lot", "Thanks so much, that really helped a lot."),
    ("um i added you to the shared folder", "I added you to the shared folder."),
    ("so yeah lets keep the scope tight for this one", "So yeah, let's keep the scope tight for this one."),
]
for raw, clean in PLN: P(raw, clean, "plain")

# ============ REINFORCE: minimal edit (restraint) ============
MIN = [
    ("the quarterly numbers came in higher than expected", "The quarterly numbers came in higher than expected."),
    ("please review the attached document by end of day", "Please review the attached document by end of day."),
    ("the team did an outstanding job on this", "The team did an outstanding job on this."),
    ("let me know what time works best for you", "Let me know what time works best for you."),
    ("the contract has been signed and returned", "The contract has been signed and returned."),
    ("our flight leaves early so set an alarm", "Our flight leaves early, so set an alarm."),
    ("congratulations on the well deserved promotion", "Congratulations on the well deserved promotion."),
    ("the new hire starts on the first", "The new hire starts on the first."),
]
for raw, clean in MIN: P(raw, clean, "minimal_edit")

# ============ REINFORCE: proper-noun capitalization ============
NM = [
    ("i talked to sarah and michael about paris", "I talked to Sarah and Michael about Paris."),
    ("were flying into san francisco then driving to los angeles", "We're flying into San Francisco then driving to Los Angeles."),
    ("forward the deck to amanda on the netflix account", "Forward the deck to Amanda on the Netflix account."),
    ("lets meet at the starbucks on fifth avenue", "Let's meet at the Starbucks on Fifth Avenue."),
    ("i ordered the new iphone from amazon", "I ordered the new iPhone from Amazon."),
    ("david from google is joining the call", "David from Google is joining the call."),
    ("priya and raj will handle the mumbai office", "Priya and Raj will handle the Mumbai office."),
    ("we use slack and notion for everything", "We use Slack and Notion for everything."),
]
for raw, clean in NM: P(raw, clean, "names")

# ============ REINFORCE: scratch-that preserved (downstream handles it) ============
SC = [
    ("lets meet at noon scratch that lets meet at one", "Let's meet at noon. Scratch that, let's meet at one."),
    ("send it to john undo that send it to jane", "Send it to John. Undo that, send it to Jane."),
    ("book the flight for monday nevermind book it for tuesday", "Book the flight for Monday. Nevermind, book it for Tuesday."),
    ("the budget is ten thousand scratch that twelve thousand", "The budget is 10,000. Scratch that, 12,000."),
]
for raw, clean in SC: P(raw, clean, "scratch")

# ============ REINFORCE: long run-on (sentence breaks) ============
RO = [
    ("so basically what happened was i woke up late missed the bus and had to call a cab",
     "So basically what happened was I woke up late, missed the bus, and had to call a cab."),
    ("we need to talk about the budget because were over on marketing and the costs keep rising",
     "We need to talk about the budget because we're over on marketing and the costs keep rising."),
    ("i went to the store and they were out of everything no eggs no milk no bread",
     "I went to the store and they were out of everything: no eggs, no milk, no bread."),
]
for raw, clean in RO: P(raw, clean, "long_runon")

# ============ BATCH 2: more weak-spot variety ============
CODE2 = [
    ("the use memo hook is expensive", "The useMemo hook is expensive."),
    ("the use ref points to the input", "The useRef points to the input."),
    ("call set state inside the handler", "Call setState inside the handler."),
    ("the fetch data async function hangs", "The fetchData async function hangs."),
    ("the is logged in boolean is false", "The isLoggedIn boolean is false."),
    ("update the user profile component", "Update the UserProfile component."),
    ("the base url constant is wrong", "The BASE_URL constant is wrong."),
    ("we parse the xml then the json", "We parse the XML then the JSON."),
    ("the graphql endpoint is slow", "The GraphQL endpoint is slow."),
    ("its a jpeg not a png", "It's a JPEG not a PNG."),
    ("export it as a pdf or csv", "Export it as a PDF or CSV."),
    ("the ip address changed", "The IP address changed."),
    ("ssh into the box and check", "SSH into the box and check."),
    ("the dns record is missing", "The DNS record is missing."),
    ("run it through the gpu", "Run it through the GPU."),
    ("the ram usage spiked", "The RAM usage spiked."),
    ("we use tcp not udp", "We use TCP not UDP."),
    ("the uuid is duplicated", "The UUID is duplicated."),
    ("hit the slash health endpoint", "Hit the /health endpoint."),
    ("the file is main dot rs", "The file is main.rs."),
    ("import it from at slash components", "Import it from @/components."),
    ("the to do list state is stale", "The toDoList state is stale."),
    ("we call the on submit callback", "We call the onSubmit callback."),
    ("the api gateway returned a timeout", "The API gateway returned a timeout."),
    ("the kafka consumer lagged", "The Kafka consumer lagged."),
    ("spin up an ec two instance", "Spin up an EC2 instance."),
    ("the s three bucket is public", "The S3 bucket is public."),
    ("check the cpu and the gpu", "Check the CPU and the GPU."),
]
for raw, clean in CODE2: P(raw, clean, "code")

NUM2 = [
    ("we got a four oh three forbidden", "We got a 403 forbidden."),
    ("upgrade to version three point two point one", "Upgrade to version 3.2.1."),
    ("listen on port three thousand", "Listen on port 3000."),
    ("the deadline is the fifteenth", "The deadline is the 15th."),
    ("she turns thirty next month", "She turns 30 next month."),
    ("we sold over ten thousand units", "We sold over 10,000 units."),
    ("the call is at eleven fifteen", "The call is at 11:15."),
    ("its negative ten degrees outside", "It's negative 10 degrees outside."),
    ("the budget grew by fifty percent", "The budget grew by 50 percent."),
    ("read pages forty to forty five", "Read pages 40 to 45."),
    ("the score was three to one", "The score was 3 to 1."),
    ("we have twenty four seven support", "We have 24/7 support."),
    ("the model is point eight billion parameters", "The model is 0.8 billion parameters."),
    ("interest is at five point five percent", "Interest is at 5.5 percent."),
    ("the train leaves at six oh five", "The train leaves at 6:05."),
]
for raw, clean in NUM2: P(raw, clean, "number")

AMBIG2 = [
    ("youre right that its broken", "You're right that it's broken."),
    ("theyre saying its too late", "They're saying it's too late."),
    ("whos going to the offsite", "Who's going to the offsite?"),
    ("lets see whats on the agenda", "Let's see what's on the agenda."),
    ("theres a problem with the build", "There's a problem with the build."),
    ("heres the thing about the deadline", "Here's the thing about the deadline."),
    ("its raining so well drive", "It's raining, so we'll drive."),
    ("im sure youll figure it out", "I'm sure you'll figure it out."),
    ("the team did its best", "The team did its best."),            # possessive
    ("he lets the intern lead", "He lets the intern lead."),        # verb
    ("i was ill all weekend", "I was ill all weekend."),            # sick
    ("things went well overall", "Things went well overall."),      # adverb
    ("we were both surprised", "We were both surprised."),          # past
    ("the garden shed needs paint", "The garden shed needs paint."),# noun, NOT she'd
    ("its been great working with you", "It's been great working with you."),
    ("well need more time on this", "We'll need more time on this."),
    ("id rather wait until friday", "I'd rather wait until Friday."),
    ("hes been out sick this week", "He's been out sick this week."),
]
for raw, clean in AMBIG2: P(raw, clean, "contraction_ambig")

# ----- assemble -----
SYS = cleanup_prompt("verbatim")
out = Path(__file__).resolve().parent / "data" / "trainset_weakness.jsonl"
with out.open("w") as f:
    for raw, clean, cat in PAIRS:
        rec = {"messages": [
            {"role": "system", "content": SYS},
            {"role": "user", "content": WRAP.format(raw=raw)},
            {"role": "assistant", "content": clean},
        ], "category": cat, "style": "verbatim", "teacher": "claude-opus-4-8 (authored)"}
        f.write(json.dumps(rec) + "\n")

import collections
mix = collections.Counter(c for _, _, c in PAIRS)
print(f"authored {len(PAIRS)} pairs -> {out}")
for k, v in sorted(mix.items(), key=lambda x: -x[1]):
    print(f"  {k:18} {v}")
