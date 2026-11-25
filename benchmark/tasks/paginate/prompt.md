I have a long list of search results and want to show them a few at a time, on pages. Write a function called
`paginate` that takes the list, the page number and how many results fit on one page, and gives back the results
for that page. The first page is page 1. Asking for a page after the last one should just give back an empty list,
and a page number or page size below 1 doesn't make sense, so it should cause an error.
