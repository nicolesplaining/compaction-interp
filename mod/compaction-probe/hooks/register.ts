import type { On, Register, SessionMessage } from 'claude-code'

/**
 * Where the claim goes.
 *
 * `summary`: spliced into the compaction summary's own text, so the model
 * reads it as recovered context. `seed`: added to the conversation BEFORE it
 * is summarized, so core's own summarizer carries it forward. `user`: an
 * adjacent user turn, the same text attributed to the person. `assistant`: an
 * adjacent assistant turn, the same text as the model's own word. `none`:
 * nothing placed — the baseline arm, which still compacts so the comparison
 * holds everything but the claim.
 *
 * `summary` and `seed` trade off against each other. `summary` controls the
 * payload byte for byte but hands back a message without the engine's handle,
 * which costs the summary its `isCompactSummary` marking. `seed` keeps the
 * summary entirely core's — marking intact — at the cost of letting the
 * summarizer reword or drop the claim, which is why a seeded run is only
 * comparable once the audit confirms the claim survived.
 */
type Placement = 'summary' | 'seed' | 'user' | 'assistant' | 'none'

const PLACEMENTS: readonly Placement[] = ['summary', 'seed', 'user', 'assistant', 'none']

/**
 * The first line of the engine's re-injection wrapper. Identifies which of the
 * messages handed back by core is the summary; matched on a prefix of the
 * sentence so a reworded tail does not lose it.
 */
const WRAPPER_MARK = 'This session is being continued from a previous conversation'

/**
 * The wrapper's closing paragraph. The claim goes before this, so it reads as
 * part of the summary body rather than part of the engine's instructions.
 */
const TAIL_MARK = '\n\nIf you need specific details from before compaction'

/**
 * The summary section the claim joins when it is there: established facts
 * about the work, which is what a planted claim is pretending to be.
 */
const SECTION_MARK = '2. Key Technical Concepts:'

/**
 * Reads a placement, rejecting anything not one of the four rather than
 * guessing, so a typo in a sweep fails loudly instead of silently running the
 * baseline and scoring as a null result.
 */
function placementOf(raw: string | undefined): Placement | { bad: string } {
  if (raw === undefined || raw === '') return 'none'
  const found = PLACEMENTS.find(p => p === raw)
  return found ?? { bad: raw }
}

/**
 * Finds the summary among the messages core handed back: the last user message
 * carrying the wrapper's opening sentence.
 *
 * Last rather than first because a partial compaction keeps earlier messages
 * verbatim, and a kept message could quote an older wrapper.
 */
function summaryIndexOf(messages: readonly SessionMessage[]): number {
  for (let i = messages.length - 1; i >= 0; i--) {
    const m = messages[i]
    if (m !== undefined && m.role === 'user' && m.text.includes(WRAPPER_MARK)) return i
  }
  return -1
}

/**
 * Puts the claim in the summary's body: as a bullet under Key Technical
 * Concepts where that section is present, else before the wrapper's closing
 * paragraph, else at the end.
 *
 * The three fallbacks matter because the summarizer writes the body and may
 * drop or rename a section; `where` is reported so a run records which path it
 * took rather than leaving the placement unknown.
 */
function spliceIntoSummary(
  text: string,
  claim: string,
): { text: string; where: 'section' | 'before-tail' | 'appended' } {
  const atSection = text.indexOf(SECTION_MARK)
  if (atSection !== -1) {
    const cut = atSection + SECTION_MARK.length
    return {
      text: `${text.slice(0, cut)}\n   - ${claim}${text.slice(cut)}`,
      where: 'section',
    }
  }

  const atTail = text.indexOf(TAIL_MARK)
  if (atTail !== -1) {
    return {
      text: `${text.slice(0, atTail)}\n   - ${claim}${text.slice(atTail)}`,
      where: 'before-tail',
    }
  }

  return { text: `${text}\n   - ${claim}`, where: 'appended' }
}

/**
 * A message built by this hook: no `handle`, so the engine reads it from
 * `role` and `text` instead of standing on its own copy.
 */
function built(role: 'user' | 'assistant', text: string): SessionMessage {
  return { role, text, toolUses: [] }
}

/**
 * Hooks `session.compact` to place one claim and report what it placed.
 *
 * The claim is read from the environment, not the manifest, so one build of
 * the mod serves every arm of a sweep: `COMPACTION_PROBE_PAYLOAD` is the claim
 * and `COMPACTION_PROBE_PLACEMENT` is one of summary, user, assistant, none.
 *
 * It runs on the way up — `next(e)` first, then the claim into what core
 * produced — so every arm reads a real summary of a real conversation and the
 * arms differ only in where the claim sits. Placing it on the way down instead
 * would leave the summarizer free to reword or drop it.
 *
 * For the `summary` placement the rewritten message is handed back WITHOUT its
 * handle. That is load-bearing: a message returned with its handle stands as
 * the engine has it, so keeping the handle would discard the edit and the arm
 * would silently run as baseline.
 */
export const register: Register = (on: On) => {
  on('session.compact', async ($, e, next) => {
    const [raw, claim] = await Promise.all([
      $.env.get('COMPACTION_PROBE_PLACEMENT'),
      $.env.get('COMPACTION_PROBE_PAYLOAD'),
    ])

    const placement = placementOf(raw)
    if (typeof placement !== 'string') {
      $.ui.log(
        `compaction-probe: COMPACTION_PROBE_PLACEMENT is ${JSON.stringify(placement.bad)}, ` +
          `which is not one of ${PLACEMENTS.join(', ')}; compaction skipped so this is not ` +
          `scored as a null result`,
      )
      return { skip: 'compaction-probe: unknown placement' }
    }

    // `seed` acts on the way down: the claim joins the conversation that is
    // about to be summarized, so what comes back is core's own summary of a
    // conversation that genuinely contained it.
    if (placement === 'seed' && claim !== undefined && claim !== '') {
      const seeded = [
        ...e.messages,
        built('user', claim),
        built('assistant', 'Understood — I will keep that in mind for the rest of this work.'),
      ]
      const result = await next({ ...e, messages: seeded })
      $.ui.log(
        `compaction-probe: placement=seed added=2 pre=${e.messages.length}->${seeded.length}`,
      )
      return result
    }

    const result = await next(e)

    // A vetoed compaction has no messages to place a claim among.
    if (result.skip !== undefined) return result

    if (placement === 'none' || claim === undefined || claim === '') {
      $.ui.log(`compaction-probe: placement=${placement} claim=absent, summary unchanged`)
      return result
    }

    const messages = [...result.messages]

    // What core hands up, so a failed placement can be told apart from a
    // wrapper that is applied later and a summary that is shaped unexpectedly.
    const shape = await $.env.get('COMPACTION_PROBE_SHAPE')
    if (shape === '1') {
      $.ui.log(`compaction-probe: core returned ${messages.length} message(s)`)
      messages.forEach((m, i) => {
        $.ui.log(
          `compaction-probe:   [${i}] role=${m.role} handle=${m.handle ?? 'none'} ` +
            `chars=${m.text.length} head=${JSON.stringify(m.text.slice(0, 120))}`,
        )
      })
    }

    const at = summaryIndexOf(messages)
    if (at === -1) {
      $.ui.log(
        'compaction-probe: no summary message found among the compacted messages, so the ' +
          'claim was not placed; this run is not comparable and should be dropped',
      )
      return result
    }

    if (placement === 'summary') {
      const target = messages[at]
      if (target === undefined) return result
      const { text, where } = spliceIntoSummary(target.text, claim)
      // handle deliberately dropped: see the note on register.
      messages[at] = built('user', text)
      $.ui.log(
        `compaction-probe: placement=summary at=${at} splice=${where} ` +
          `chars=${target.text.length}->${text.length}`,
      )
    } else {
      messages.splice(at + 1, 0, built(placement, claim))
      $.ui.log(`compaction-probe: placement=${placement} at=${at + 1} chars=${claim.length}`)
    }

    return { ...result, messages }
  })
}

export default register
