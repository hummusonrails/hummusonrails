require 'httparty'
require 'octokit'
require 'base64'
require 'json'

# Fetch blog posts from the API
url = "https://www.bengreenberg.dev/api/posts.json"
response = HTTParty.get(url)
posts = JSON.parse(response.body)

# Generate the updated blog posts list (top 5)
posts_list = ["\n### Recent Blog Posts\n\n"]
posts.first(5).each do |post|
  title = post['title']
  link = "https://www.bengreenberg.dev/blog/#{post['slug']}"
  posts_list << "* [#{title}](#{link})"
end

# Update the README.md file
client = Octokit::Client.new(access_token: ENV['GITHUB_TOKEN'])
repo = ENV['GITHUB_REPOSITORY']
readme = client.readme(repo)
readme_content = Base64.decode64(readme[:content]).force_encoding('UTF-8')

# Replace the existing blog posts section
posts_regex = /### Recent Blog Posts\n\n[\s\S]*?(?=\n<\/td>|\n##|\z)/m
updated_content = readme_content.sub(posts_regex, "#{posts_list.join("\n")}\n")

client.update_contents(repo, 'README.md', 'Update recent blog posts', readme[:sha], updated_content)
