"""SQLAlchemy models.

Teaching-core isolation lives here: the ``reviews`` table carries no graded
column of any kind. Supervisor scoring has its own table (``scorecards``),
its own rows, and its own read path.
"""

from app.models.projection import Projection
from app.models.review import Review
from app.models.rubric import Rubric
from app.models.scorecard import Scorecard
from app.models.transcript import Transcript

__all__ = ["Projection", "Review", "Rubric", "Scorecard", "Transcript"]
