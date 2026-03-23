"""
Intervention Detection System
Ported from mello-android/utils/interventions.ts

Detects emotional states requiring specialized guidance:
- suicidal_tendencies (priority 110)
- crisis (priority 100)
- breathing/panic (priority 80)
- trauma (priority 70)
- work_exhaustion (priority 60)
- loneliness (priority 50)
- emotional_processing (priority 40)
"""

import re
import time
from typing import TypedDict, Literal, Any

# ═══════════════════════════════════════════════════
# TYPES
# ═══════════════════════════════════════════════════

InterventionType = Literal[
    'suicidal_tendencies',
    'crisis',
    'breathing',
    'work_exhaustion',
    'loneliness',
    'trauma',
    'emotional_processing'
]

class InterventionDecision(TypedDict):
    type: InterventionType
    guidance: str
    ttl_ms: int
    priority: int
    cooldown_ms: int

class InterventionDetectorState(TypedDict):
    last_decision_type: InterventionType | None
    last_decision_at: float | None
    history: list[dict]

# ═══════════════════════════════════════════════════
# CONSTANTS
# ═══════════════════════════════════════════════════

DEFAULT_TTL_MS = 120_000
DEFAULT_COOLDOWN_MS = 90_000
MIN_TRANSCRIPT_LENGTH = 15

# ═══════════════════════════════════════════════════
# DETECTION PATTERNS (exact port from TS)
# ═══════════════════════════════════════════════════

CRISIS_REGEX = re.compile(
    r"\b(end it all|can't do this anymore|cannot do this anymore|done with life|"
    r"want this to stop|don't want to be here|do not want to be here|can't go on|"
    r"cannot go on|no point anymore)\b",
    re.IGNORECASE
)

SUICIDAL_REGEX = re.compile(
    r"\b(suicide|suicidal|kill myself|end my life|want to die|don't want to live|"
    r"do not want to live|hurt myself|harm myself|better off dead|wish i were dead)\b",
    re.IGNORECASE
)

SUICIDAL_INDIRECT_REGEX = re.compile(
    r"\b(want to disappear|wish i could disappear|better without me|should not be here)\b",
    re.IGNORECASE
)

PANIC_REGEX = re.compile(
    r"\b(panic(?:king|ked)?|panic attack|anxious|anxiety attack|overwhelm(?:ed|ing)?|"
    r"can'?t breathe|cannot breathe|hard(?:er)? to breathe|freaking out|chest (?:is )?tight|"
    r"heart (?:is )?racing|spiral(?:ing|ed)?)\b",
    re.IGNORECASE
)

WORK_EXHAUSTION_REGEX = re.compile(
    r"\b(work(?:ing)? all (?:the )?time|can'?t stop work(?:ing)?|cannot stop work(?:ing)?|"
    r"always work(?:ing)?|burn(?:ed|t)? out|burnout|exhausted (?:from|by) work|"
    r"work (?:is )?everything|work (?:like )?\d+ hours?|work eighty hours|eighty hours a week|"
    r"keep pushing myself|can'?t (?:seem to )?stop|even on weekends|never stop work(?:ing)?|"
    r"always on (?:the )?clock|work all weekend|i (?:just )?keep work(?:ing)?|i only work)\b",
    re.IGNORECASE
)

LONELINESS_REGEX = re.compile(
    r"\b(lonely|(?:all )?alone|isolated|nobody understands(?: me)?|no (?:real )?friends|"
    r"feel(?:ing)? disconnected|feel(?:ing)? left out|no one gets me|i have nobody|"
    r"no one to talk to|i'?m (?:so )?alone|nobody cares)\b",
    re.IGNORECASE
)

TRAUMA_REGEX = re.compile(
    r"\b(trauma|abuse|assault|attacked|harassed|molested|violated|flashback|ptsd|"
    r"what happened to me|unsafe after|after what happened)\b",
    re.IGNORECASE
)

EMOTIONAL_PROCESSING_REGEX = re.compile(
    r"\b(i don't know what i'm feeling|i dont know what i'm feeling|"
    r"i can't understand my feelings|i cant understand my feelings|i feel numb|"
    r"i'm numb|im numb|confused about how i feel|trying to understand my feelings|"
    r"what am i feeling|why do i feel this way|mixed feelings|all over the place emotionally)\b",
    re.IGNORECASE
)

GOODBYE_REGEX = re.compile(r"\b(goodbye)\b", re.IGNORECASE)
FEELING_REGEX = re.compile(r"\b(feel|feeling|understand|why|what)\b", re.IGNORECASE)

# ═══════════════════════════════════════════════════
# INTERVENTION GUIDANCE (exact from TS)
# ═══════════════════════════════════════════════════

INTERVENTIONS = {
    'suicidal_tendencies': {
        'guidance': (
            'User may have suicidal thoughts or self-harm intent. Respond with calm, direct empathy '
            'and prioritize immediate safety. Ask if they are in immediate danger or thinking of acting '
            'on it now. Strongly encourage contacting a trusted person, emergency help, or a crisis line '
            'right away. Offer one concrete next step like calling someone now, moving away from anything '
            'dangerous, unlocking the door, or staying connected while they reach out. Do not stay abstract '
            'or overly exploratory.'
        ),
        'ttl_ms': 180_000,
        'priority': 110,
        'cooldown_ms': 15_000,
    },
    'crisis': {
        'guidance': (
            'User may be in crisis. Respond with calm empathy and prioritize immediate safety over '
            'exploration. Ask if they are in immediate danger and encourage urgent human support now: '
            'a trusted person, local emergency help, or a crisis line. Offer one tiny next step like '
            'putting distance between them and anything dangerous, calling someone now, or staying on '
            'the line while they reach out. Keep it brief and direct.'
        ),
        'ttl_ms': 180_000,
        'priority': 100,
        'cooldown_ms': 15_000,
    },
    'breathing': {
        'guidance': (
            'When panic, anxiety, or overwhelm is high, first validate briefly, then ask consent for '
            'one short grounding technique. Prefer box breathing with a slow count, and if the user says '
            'breathing feels hard, offer butterfly hug tapping or the five four three two one grounding '
            'exercise instead. After the technique, ask what changed in their body by even five percent. '
            'Keep it calm, practical, and solution focused.'
        ),
        'ttl_ms': DEFAULT_TTL_MS,
        'priority': 80,
        'cooldown_ms': DEFAULT_COOLDOWN_MS,
    },
    'trauma': {
        'guidance': (
            'When the user shares trauma, abuse, or feeling unsafe after something major, validate the '
            'weight of it without pushing for details. Focus on present safety and stabilization. Offer '
            'one grounding option like feet on the floor, looking around the room, or butterfly hug tapping. '
            'Ask what would help them feel even five percent safer right now, and gently encourage reaching '
            'out to a trusted person if appropriate.'
        ),
        'ttl_ms': DEFAULT_TTL_MS,
        'priority': 70,
        'cooldown_ms': DEFAULT_COOLDOWN_MS,
    },
    'work_exhaustion': {
        'guidance': (
            'When the user sounds trapped in overwork, validate the exhaustion and help them notice the '
            'pattern between pressure, fear, and constant work. Ask one curious question about what feels '
            'risky about resting. Offer one small experiment, such as a ten minute pause, stopping one task '
            'tonight, or protecting one hour this weekend. Keep it concrete, supportive, and nonjudgmental.'
        ),
        'ttl_ms': DEFAULT_TTL_MS,
        'priority': 60,
        'cooldown_ms': DEFAULT_COOLDOWN_MS,
    },
    'loneliness': {
        'guidance': (
            'When the user expresses loneliness or isolation, validate the ache of being alone and help '
            'them identify where connection has felt even slightly safer before. Ask about one person, '
            'place, or routine that makes them feel five percent less alone. Offer one tiny step like '
            'sending one text, sitting near people, or making one plan for today. Keep it warm and practical.'
        ),
        'ttl_ms': DEFAULT_TTL_MS,
        'priority': 50,
        'cooldown_ms': DEFAULT_COOLDOWN_MS,
    },
    'emotional_processing': {
        'guidance': (
            'When the user is trying to understand their feelings or feels numb or confused, help them '
            'label the experience gently. Ask about three anchors: what happened, what they feel in the '
            'body, and what emotion word fits best even if it is only a guess. If useful, offer simple '
            'options like sad, angry, scared, guilty, lonely, or numb. Then ask what the feeling may be '
            'needing right now.'
        ),
        'ttl_ms': DEFAULT_TTL_MS,
        'priority': 40,
        'cooldown_ms': DEFAULT_COOLDOWN_MS,
    },
}

# ═══════════════════════════════════════════════════
# HELPER FUNCTIONS
# ═══════════════════════════════════════════════════

def normalize_key(value: str) -> str:
    """Normalize emotion key for matching."""
    return re.sub(r'[^a-z0-9]+', '', value.lower())


def has_emotion(emotions: dict[str, float], emotion_names: list[str], threshold: float) -> bool:
    """Check if any of the emotion names exceed threshold."""
    for emotion_name in emotion_names:
        normalized = normalize_key(emotion_name)
        # Check exact match
        if normalized in emotions and emotions[normalized] >= threshold:
            return True
        # Check partial match
        for key, value in emotions.items():
            if normalized in key and value >= threshold:
                return True
    return False


def has_emotion_combination(
    emotions: dict[str, float],
    combinations: list[dict],
    require_all: bool = False
) -> bool:
    """Check for emotion combinations."""
    matches = [
        has_emotion(emotions, combo['emotions'], combo['threshold'])
        for combo in combinations
    ]
    return all(matches) if require_all else any(matches)


def looks_like_emotion_record(record: dict) -> bool:
    """Check if dict looks like emotion scores."""
    numeric_entries = [(k, v) for k, v in record.items() if isinstance(v, (int, float))]
    if not numeric_entries:
        return False
    emotion_keywords = ['anxiety', 'distress', 'sadness', 'fear', 'stress', 'tired', 'fatigue']
    return any(
        any(kw in normalize_key(key) for kw in emotion_keywords)
        for key, _ in numeric_entries
    )


def find_emotion_record(value: Any) -> dict | None:
    """Recursively find emotion scores in nested structure."""
    if not value or not isinstance(value, dict):
        return None

    # Check direct paths
    for key in ['scores', 'emotions', 'prosody', 'language']:
        direct = value.get(key)
        if direct and isinstance(direct, dict):
            if looks_like_emotion_record(direct):
                return direct
            nested_scores = direct.get('scores')
            if nested_scores and isinstance(nested_scores, dict) and looks_like_emotion_record(nested_scores):
                return nested_scores

    # Recurse into nested values
    for nested_value in value.values():
        found = find_emotion_record(nested_value)
        if found:
            return found

    return None


def extract_emotion_scores(models: Any) -> dict[str, float]:
    """Extract normalized emotion scores from Hume models data."""
    raw_scores = find_emotion_record(models)
    if not raw_scores:
        return {}

    normalized = {}
    for key, value in raw_scores.items():
        if not isinstance(value, (int, float)) or value != value:  # NaN check
            continue
        if value < 0.05:
            continue
        normalized[normalize_key(key)] = float(value)

    return normalized


# ═══════════════════════════════════════════════════
# PUBLIC API
# ═══════════════════════════════════════════════════

def get_initial_state() -> InterventionDetectorState:
    """Create initial intervention detector state."""
    return {
        'last_decision_type': None,
        'last_decision_at': None,
        'history': [],
    }


def detect_intervention(
    message: dict,
    state: InterventionDetectorState,
    now: float | None = None
) -> InterventionDecision | None:
    """
    Detect if intervention is needed based on user message.

    Args:
        message: Hume user_message with 'message' and 'models' fields
        state: Detector state for cooldown tracking
        now: Current timestamp in ms (default: current time)

    Returns:
        InterventionDecision if intervention needed, None otherwise
    """
    if now is None:
        now = time.time() * 1000  # Convert to ms

    msg_type = message.get('type', '')
    if msg_type != 'user_message':
        return None

    transcript = (message.get('message') or {}).get('content', '').strip()
    if not transcript:
        return None

    # Short transcripts skip detection unless crisis/suicidal
    if len(transcript) < MIN_TRANSCRIPT_LENGTH:
        if not (SUICIDAL_REGEX.search(transcript) or CRISIS_REGEX.search(transcript)):
            return None

    emotions = extract_emotion_scores(message.get('models', {}))

    # Check rules in priority order
    rules_to_check = [
        # Suicidal tendencies (priority 110)
        (
            'suicidal_tendencies',
            lambda: (
                SUICIDAL_REGEX.search(transcript) is not None or
                (SUICIDAL_INDIRECT_REGEX.search(transcript) is not None and
                 has_emotion(emotions, ['sadness', 'distress', 'pain'], 0.4))
            )
        ),
        # Crisis (priority 100)
        (
            'crisis',
            lambda: (
                CRISIS_REGEX.search(transcript) is not None or
                (GOODBYE_REGEX.search(transcript) is not None and
                 has_emotion(emotions, ['distress', 'sadness', 'pain', 'fear'], 0.35))
            )
        ),
        # Breathing/panic (priority 80)
        (
            'breathing',
            lambda: (
                has_emotion(emotions, ['anxiety', 'distress', 'fear', 'stress', 'horror'], 0.5) or
                PANIC_REGEX.search(transcript) is not None
            )
        ),
        # Work exhaustion (priority 60) - checked before loneliness/trauma per TS order
        (
            'work_exhaustion',
            lambda: (
                WORK_EXHAUSTION_REGEX.search(transcript) is not None and
                has_emotion_combination(emotions, [
                    {'emotions': ['tiredness', 'fatigue'], 'threshold': 0.2},
                    {'emotions': ['stress', 'anxiety', 'distress', 'disappointment'], 'threshold': 0.2},
                ], require_all=True)
            )
        ),
        # Loneliness (priority 50)
        (
            'loneliness',
            lambda: (
                LONELINESS_REGEX.search(transcript) is not None and
                has_emotion(emotions, ['sadness', 'distress', 'pain'], 0.35)
            )
        ),
        # Trauma (priority 70) - checked after loneliness per TS order
        (
            'trauma',
            lambda: (
                TRAUMA_REGEX.search(transcript) is not None and
                has_emotion(emotions, ['distress', 'fear', 'sadness', 'pain'], 0.3)
            )
        ),
        # Emotional processing (priority 40)
        (
            'emotional_processing',
            lambda: (
                EMOTIONAL_PROCESSING_REGEX.search(transcript) is not None or
                (has_emotion(emotions, ['confusion', 'contemplation', 'doubt', 'sadness'], 0.3) and
                 FEELING_REGEX.search(transcript) is not None)
            )
        ),
    ]

    for intervention_type, matches_fn in rules_to_check:
        if not matches_fn():
            continue

        config = INTERVENTIONS[intervention_type]

        # Check cooldown
        if (state['last_decision_type'] == intervention_type and
            state['last_decision_at'] is not None and
            now - state['last_decision_at'] < config['cooldown_ms']):
            return None

        # Update state
        state['last_decision_type'] = intervention_type
        state['last_decision_at'] = now
        state['history'].append({
            'type': intervention_type,
            'timestamp': now,
        })
        # Keep only last 10 minutes of history
        state['history'] = [
            entry for entry in state['history']
            if now - entry['timestamp'] < 600_000
        ]

        return {
            'type': intervention_type,
            'guidance': config['guidance'],
            'ttl_ms': config['ttl_ms'],
            'priority': config['priority'],
            'cooldown_ms': config['cooldown_ms'],
        }

    return None


def is_high_priority(intervention_type: InterventionType) -> bool:
    """Check if intervention type requires auto-expanded crisis resources."""
    return intervention_type in ('suicidal_tendencies', 'crisis')
