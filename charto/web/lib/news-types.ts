/**
 * news-types — shared types for fetch.news / trigger.event workflow steps.
 * These are the shapes the backend executor will populate on step output.
 */

export type NewsArticle = {
  id: string;
  title: string;
  description: string;
  source: string;
  source_id: string;
  url: string;
  published_at: string;
  credibility_score: number;
  match_confidence: number | null;
  matched: boolean;
  reason: string | null;
};

export type NewsStepConfig = {
  keywords?: string[];
  event_description?: string;
  sources?: string[];
  min_confidence?: number;
  hours_back?: number;
};

export type NewsRunOutput = {
  articles: NewsArticle[];
  matched: boolean;
  matched_count: number;
  max_confidence: number;
  top_article: NewsArticle | null;
  event_description: string;
};
