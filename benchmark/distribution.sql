-- rows_by_split
SELECT split, COUNT(*) AS n,
 SUM(text_only) AS text, SUM(1-text_only) AS image, MIN(level) AS min_level,MAX(level) AS max_level,
 SUM(text_only IS NULL) AS missing
 FROM problems GROUP BY split;

-- duplicates
SELECT COUNT(*) AS id_language_keys, SUM(c=2) AS across_two_splits
 FROM (SELECT id,language,COUNT(DISTINCT split) c FROM problems GROUP BY id,language);

-- conflicts
SELECT COUNT(*) AS conflicting_ids FROM (
 SELECT id FROM problems GROUP BY id HAVING MIN(level)<>MAX(level) OR MIN(text_only)<>MAX(text_only));

-- unique_by_level
WITH ids AS (SELECT id,MIN(level) AS level,MIN(text_only) AS text_only
 FROM problems GROUP BY id)
 SELECT level,COUNT(*) AS n,SUM(text_only) AS text,SUM(1-text_only) AS image
 FROM ids GROUP BY level ORDER BY level;

-- unique_by_split
WITH ids AS (SELECT split,id,MIN(text_only) AS text_only FROM problems GROUP BY split,id)
 SELECT split,COUNT(*) AS n,SUM(text_only) AS text,SUM(1-text_only) AS image FROM ids GROUP BY split;

-- languages
SELECT language,COUNT(*) AS n, SUM(text_only) AS text,SUM(1-text_only) AS image
 FROM (SELECT id,language,MIN(text_only) AS text_only FROM problems WHERE language IN ('cat','eng','esp') GROUP BY id,language)
 GROUP BY language;
