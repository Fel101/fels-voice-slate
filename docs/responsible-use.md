# Responsible use

Voice Slate helps you make clean, well-documented recordings of a voice. Those recordings can be used for good
things (a text-to-speech voice for someone who is losing theirs, a speech dataset, narration, game dialogue) and for
harmful ones (impersonation and fraud). A person's voice is part of who they are, so this program is built around
consent, and this page explains what that means in practice.

This page is general guidance, **not legal advice**. The rules differ between countries and states and they change. If
you are unsure, ask a lawyer.

## The short version

- Record **your own voice**, or the voice of someone who has **clearly agreed** to what you plan to do with it.
- When the voice is someone else's, get that agreement **in writing**, and keep it.
- Never record people without their knowledge, and never use a recording to impersonate, deceive or harass anyone.
- Treat every recording as personal data: keep it private and delete it when it is no longer needed or when asked.
- Check the rules of any tool, service or platform you give the clips to.

## What Voice Slate asks you

Before it will keep a clip, the program asks for three things and saves them in the clip's sidecar file, next to the
audio:

| Field | What to write | Example |
|---|---|---|
| **Origin** | Where the recording came from. | *Recorded by me at my desk, 2026-10-20* or *Recorded in my studio by the speaker, release dated 2026-09-02* |
| **Allowed use** | What the voice may be used for, and what it may not. | *Personal narration voice, not shared* or *Training the game's narrator voice, release v2* |
| **Permission box** | Your confirmation that you have the right to use this voice for that purpose: it is your own voice, or the speaker has given permission. | |

The origin and allowed use are remembered for each speaker. The permission box is never remembered, so you are asked
again for every clip.

These are **your statements**. The program saves them but cannot check them, and the tick box is not a legal release. It
is there to make you stop and think, and to leave a record of what you decided and when. For someone else's voice,
keep a proper written release as well.

## Whose voice you can record

**Your own.** You can record and use your own voice as you like, subject to the rules of the tools you use it with.

**Someone who has agreed.** A good agreement says, in plain words:

- who the person is and that they agree to be recorded;
- what the recordings may be used for (for example "to make a synthetic voice for this project") and what they may not
  be used for;
- whether the clips or any synthetic voice made from them may be shared, published or sold;
- how long the permission lasts, and how the person can withdraw it;
- whether and how they are paid. Professional voice actors may have contracts or union rules that already cover this.

Children need the agreement of a parent or guardian. Do not record anyone who cannot understand and agree.

**Not someone else's audio you happened to find.** Audio from videos, streams, podcasts, calls, films or broadcasts is
not yours to use because you are able to capture it. The **System audio** source records what your computer plays. Use it
for audio you own or have permission to use. Voice Slate does not try to get around copy protection, and nothing in
it is meant to.

## What not to use it for

Do not use Voice Slate, or clips made with it, to:

- **impersonate** a real person, or pass a synthetic voice off as theirs, to deceive, defraud or embarrass anyone;
- make **scam or robocalls**, fake endorsements, or fake statements by real people;
- **bypass voice authentication** (a bank's or phone's "my voice is my password" check);
- make **harassing, defamatory or sexual** material about a real person, or any sexual material of a minor;
- record people **covertly**, or record conversations without the consent the law requires.

Changes that would make these easier, such as hiding or skipping the consent step, or removing the origin and allowed-use
fields, will not be accepted into this project.

## Handling the recordings

A recording of a voice is **personal data**, and a voiceprint can identify a person. Treat it with care:

- **Keep it on your computer.** Voice Slate makes no network connections. Where the files go after that is up to you.
- **Mind synced folders.** By default the recordings go in a `voice-clips` folder beside the program, which Git ignores.
  If the program's own folder is one that OneDrive, Dropbox or similar syncs, or cannot be written to, the program uses
  `C:\Users\<you>\Recordings\voice-clips` instead, outside OneDrive's usual folders on purpose. If you point the output
  folder at a synced folder yourself, the recordings are copied to that service. Leave `voice-clips` out when you zip or
  share the program folder.
- **Protect the disk.** On a laptop, turn on Windows device encryption or BitLocker, in case it is lost.
- **Never publish them by accident.** Do not commit recordings to a public repository, post them in an issue, or send them
  in a bug report. This project's `.gitignore` leaves audio out, and bug reports should use synthetic or your own audio.
- **Share only for the allowed use**, and send the clip's sidecar file with it, so the permission travels with the audio.
- **Delete on request, and when you no longer need them.** **Discard** in the program moves a take's files to the
  Recycle Bin, which is not a secure erase: empty it. To remove everything for a speaker, delete the speaker's folder.
  The name stays in the speaker list, empty, until you remove its entry from `speakers.json`.

## Rules that may apply to you

This is not a full list. Which rules apply depends on where you and the speaker are, and what you do with the clips.

- **Privacy and data-protection law**, such as the EU and UK GDPR, treats a voice recording of an identifiable person as
  personal data. A voice processed to identify someone can be special-category biometric data, which has stricter rules.
- **Biometric privacy laws**, such as the Illinois Biometric Information Privacy Act, cover voiceprints in some US states.
- **Personality and publicity rights** protect a person's name, image and, in some places, their voice. Tennessee's ELVIS
  Act is one example.
- **Recording-consent laws** decide when you may record a conversation or a phone call, and in some places everyone on the
  call must agree.
- **Copyright and contracts** cover the audio you record from other sources, and the work of professional performers.
- **Synthetic-media rules**. Some laws and platforms require that a synthetic voice is labelled as such.
- **The terms of the tool or service** you give the clips to. Many require that you confirm you have the speaker's
  permission.

## If you make a synthetic voice from the clips

Say so wherever someone could otherwise think a real person is speaking. Many platforms and some laws require it, and
it is the honest thing to do.

## If you see misuse

Voice Slate is a local program and cannot see what anyone does with the recordings it helps make. If you find someone
using a voice without permission, report it to the platform where it appears, and to the police if it is fraud or
harassment. To report a security problem in this program, see [SECURITY.md](../SECURITY.md).
